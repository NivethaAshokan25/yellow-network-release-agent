"""
Jira Cloud REST API v3 client.
Thin wrapper around httpx — no business logic here.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SEARCH_ISSUE_FIELDS = [
    "summary",
    "description",
    "status",
    "labels",
    "issuelinks",
    "issuetype",
    "project",
]
MAX_PAGINATION_PAGES = 1000


class JiraClientError(Exception):
    """Raised on non-2xx responses from Jira."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Jira API error {status}: {message}")


class JiraClient:
    """Synchronous Jira Cloud REST v3 client using httpx."""

    def __init__(self, base_url: str, email: str, api_token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._http = httpx.Client(headers=self._headers, timeout=self._timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._http.request(method, url, **kwargs)
        if not resp.is_success:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise JiraClientError(resp.status_code, str(detail))
        if resp.text:
            return resp.json()
        return {}

    # ── Project ──────────────────────────────────────────────────────────────

    def get_project(self, project_key: str) -> Optional[Dict]:
        try:
            return self._request("GET", f"/rest/api/3/project/{project_key}")
        except JiraClientError as e:
            if e.status == 404:
                return None
            raise

    def create_project(
        self,
        key: str,
        name: str,
        project_type: str = "software",
        lead_account_id: Optional[str] = None,
    ) -> Dict:
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "projectTypeKey": project_type,
            "assigneeType": "UNASSIGNED",
        }
        if lead_account_id:
            payload["leadAccountId"] = lead_account_id
        return self._request("POST", "/rest/api/3/project", json=payload)

    # ── Issue types ───────────────────────────────────────────────────────────

    def get_issue_types_for_project(self, project_key: str) -> List[Dict]:
        return self._request("GET", f"/rest/api/3/project/{project_key}/statuses") or []

    # ── Issues ────────────────────────────────────────────────────────────────

    def create_issue(self, fields: Dict) -> Dict:
        return self._request("POST", "/rest/api/3/issue", json={"fields": fields})

    def get_issue(self, issue_key: str) -> Dict:
        return self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}",
            params={
                "fields": "summary,description,status,labels,comment,issuelinks,issuetype,project"
            },
        )

    def update_issue(self, issue_key: str, fields: Dict) -> None:
        self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": fields})

    def search_issues(self, jql: str, fields: Optional[List[str]] = None, max_results: int = 100) -> Dict:
        params: Dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields if fields is not None else SEARCH_ISSUE_FIELDS,
        }
        return self._request("GET", "/rest/api/3/search/jql", params=params)

    def search_issues_paginated(self, jql: str, fields: Optional[List[str]] = None) -> List[Dict]:
        """Fetch all pages for a JQL query."""
        all_issues: List[Dict] = []
        next_page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()
        for _ in range(MAX_PAGINATION_PAGES):
            params: Dict[str, Any] = {
                "jql": jql,
                "maxResults": 100,
                "fields": fields if fields is not None else SEARCH_ISSUE_FIELDS,
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token
            result = self._request("GET", "/rest/api/3/search/jql", params=params)
            issues = result.get("issues", [])
            page_signature = tuple(
                str(issue["key"])
                for issue in issues
                if isinstance(issue, dict) and issue.get("key") is not None
            )
            if page_signature and page_signature in seen_pages:
                raise JiraClientError(500, "Jira search repeated a results page")
            if page_signature:
                seen_pages.add(page_signature)
            all_issues.extend(issues)
            if result.get("isLast") is True:
                break
            next_page_token = result.get("nextPageToken")
            if next_page_token:
                if next_page_token in seen_tokens:
                    raise JiraClientError(500, "Jira search repeated a pagination token")
                seen_tokens.add(next_page_token)
                continue
            if result.get("isLast") is False:
                raise JiraClientError(
                    500,
                    "Jira search response indicated more results without a next-page token",
                )
            if not next_page_token:
                break
        else:
            raise JiraClientError(
                500,
                f"Jira search exceeded the {MAX_PAGINATION_PAGES}-page safety limit",
            )
        return all_issues

    # ── Comments ─────────────────────────────────────────────────────────────

    def add_comment(self, issue_key: str, body_text: str) -> Dict:
        """Add a plain-text comment (ADF format required by Jira v3)."""
        adf_body = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body_text}],
                }
            ],
        }
        return self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/comment",
            json={"body": adf_body},
        )

    def get_comments(self, issue_key: str) -> List[Dict]:
        comments: List[Dict] = []
        seen_ids: set[str] = set()
        start_at = 0
        page_size = 100
        for _ in range(MAX_PAGINATION_PAGES):
            result = self._request(
                "GET",
                f"/rest/api/3/issue/{issue_key}/comment",
                params={"startAt": start_at, "maxResults": page_size},
            )
            page = result.get("comments", [])
            page_ids = [
                str(comment["id"])
                for comment in page
                if comment.get("id") is not None
            ]
            if page_ids and not any(comment_id not in seen_ids for comment_id in page_ids):
                raise JiraClientError(
                    500,
                    f"Jira comments for {issue_key} repeated a page",
                )
            comments.extend(page)
            seen_ids.update(page_ids)

            total = result.get("total")
            if result.get("isLast") is True:
                break
            if isinstance(total, int) and start_at + len(page) >= total:
                break
            if not page:
                if isinstance(total, int) and start_at < total:
                    raise JiraClientError(
                        500,
                        f"Jira comments for {issue_key} stopped before total={total}",
                    )
                if result.get("isLast") is False:
                    raise JiraClientError(
                        500,
                        f"Jira comments for {issue_key} indicated more results without comments",
                    )
                break
            if total is None and len(page) < page_size:
                if result.get("isLast") is False:
                    raise JiraClientError(
                        500,
                        f"Jira comments for {issue_key} indicated more results without total",
                    )
                break
            start_at += len(page)
        else:
            raise JiraClientError(
                500,
                f"Jira comments for {issue_key} exceeded the "
                f"{MAX_PAGINATION_PAGES}-page safety limit",
            )
        return comments

    # ── Links ─────────────────────────────────────────────────────────────────

    def get_link_types(self) -> List[Dict]:
        result = self._request("GET", "/rest/api/3/issueLinkType")
        return result.get("issueLinkTypes", [])

    def create_issue_link(
        self,
        link_type: str,
        inward_key: str,
        outward_key: str,
    ) -> None:
        payload = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }
        self._request("POST", "/rest/api/3/issueLink", json=payload)

    # ── Labels ────────────────────────────────────────────────────────────────

    def add_label(self, issue_key: str, label: str) -> None:
        """Add a label to an existing issue without removing others."""
        issue = self.get_issue(issue_key)
        existing_labels = issue.get("fields", {}).get("labels", [])
        if label not in existing_labels:
            self.update_issue(issue_key, {"labels": existing_labels + [label]})

    # ── Myself ────────────────────────────────────────────────────────────────

    def get_myself(self) -> Dict:
        return self._request("GET", "/rest/api/3/myself")

    # ── Transitions ──────────────────────────────────────────────────────────

    def get_transitions(self, issue_key: str) -> List[Dict]:
        result = self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
        return result.get("transitions", [])

    def transition_issue(self, issue_key: str, transition_id: str) -> None:
        self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": transition_id}},
        )
