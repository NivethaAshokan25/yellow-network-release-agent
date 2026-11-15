"""Shared Jira scope, formatting, and evidence-check helpers."""
from __future__ import annotations

import re
from typing import Any, Optional

from evaluation.dimensions import Target
from jira.client import JiraClient, JiraClientError


def extract_text_from_adf(adf: Any) -> str:
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if isinstance(adf, dict):
        if adf.get("type") == "text":
            return adf.get("text", "")
        content = adf.get("content", [])
        return " ".join(extract_text_from_adf(item) for item in content)
    if isinstance(adf, list):
        return " ".join(extract_text_from_adf(item) for item in adf)
    return str(adf)


def safe_field(issue: dict, *keys: str, default: Any = None) -> Any:
    obj = issue
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, default)
    return obj


def summarise_issue(issue: dict) -> dict:
    fields = issue.get("fields") or {}
    description = fields.get("description")
    description_text = extract_text_from_adf(description) if description else ""
    source_id_match = re.search(r"\[source_id:([^\]]+)\]", description_text)
    mandatory_match = re.search(
        r"^\s*Mandatory:\s*(yes|no|true|false|required|optional)\s*$",
        description_text,
        re.IGNORECASE | re.MULTILINE,
    )
    mandatory = (
        mandatory_match.group(1).casefold() in {"yes", "true", "required"}
        if mandatory_match
        else True
    )
    return {
        "key": issue.get("key"),
        "source_id": source_id_match.group(1) if source_id_match else None,
        "mandatory": mandatory,
        "summary": fields.get("summary", ""),
        "status": safe_field(fields, "status", "name"),
        "status_category": safe_field(fields, "status", "statusCategory", "key"),
        "labels": fields.get("labels", []),
        "description": description_text[:2000],
        "issue_type": safe_field(fields, "issuetype", "name"),
        "linked_keys": [
            link.get("outwardIssue", link.get("inwardIssue", {})).get("key")
            for link in fields.get("issuelinks", [])
            if link.get("outwardIssue") or link.get("inwardIssue")
        ],
    }


def summarise_comment(comment: dict) -> dict:
    body = comment.get("body")
    return {
        "id": comment.get("id"),
        "body": extract_text_from_adf(body)[:1500] if body else "",
    }


def is_in_scope(issue: dict, project_key: str, label: str) -> bool:
    fields = issue.get("fields", {})
    issue_project = safe_field(fields, "project", "key", default="")
    labels = fields.get("labels", [])
    return str(issue_project).upper() == project_key.upper() and label in labels


def get_scoped_issue(
    client: JiraClient,
    issue_key: str,
    project_key: str,
    label: str,
) -> dict:
    issue = client.get_issue(issue_key)
    if not is_in_scope(issue, project_key, label):
        raise ValueError(f"Issue {issue_key} is outside the selected project or dataset label")
    return issue


def get_scoped_evidence_text(
    client: JiraClient,
    issue_key: str,
    project_key: str,
    label: str,
    comment_id: Optional[str] = None,
) -> tuple[str, str]:
    issue = get_scoped_issue(client, issue_key, project_key, label)
    fields = issue.get("fields", {})
    if comment_id is None:
        content = "\n".join(
            (fields.get("summary", ""), extract_text_from_adf(fields.get("description")))
        )
        return content, issue_key

    for comment in client.get_comments(issue_key):
        if str(comment.get("id")) == str(comment_id):
            return extract_text_from_adf(comment.get("body")), f"{issue_key}#comment-{comment_id}"
    raise ValueError(f"Comment {comment_id} was not found on issue {issue_key}")


def dimension_result(source_ref: str, dimension) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "dimension": dimension.dimension,
        "applicable": dimension.applicable,
        "reason": dimension.reason,
        "outcome": dimension.outcome,
    }


def jql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def run_evidence_check(
    tool_name: str,
    check_function,
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict[str, Any],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    requirement_id = arguments.get("requirement_id", "")
    issue_key = arguments.get("issue_key", "")
    try:
        evidence, source_ref = get_scoped_evidence_text(
            client,
            issue_key,
            project_key,
            label,
            arguments.get("comment_id"),
        )
        dimension = check_function(evidence, target)
        return {
            "tool": tool_name,
            "requirement_id": requirement_id,
            **dimension_result(source_ref, dimension),
            **(extra or {}),
        }
    except (JiraClientError, ValueError) as exc:
        return {
            "tool": tool_name,
            "requirement_id": requirement_id,
            "source_ref": issue_key,
            "error": str(exc),
        }
