"""Retrieve the scoped Jira release manifest."""
import json
import logging
import re
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import extract_text_from_adf, jql_literal, summarise_comment, summarise_issue
from .tool_schema import tool_definition

logger = logging.getLogger(__name__)

TOOL_DEFINITION = tool_definition(
    "get_manifest",
    "Read the release manifest from Jira within the selected project and dataset label.",
)


def get_manifest(client: JiraClient, project_key: str, label: str) -> dict[str, Any]:
    jql = (
        f"project = {jql_literal(project_key)} "
        f"AND labels = {jql_literal(label)} "
        'AND labels = "kind-manifest"'
    )
    try:
        issues = client.search_issues_paginated(jql)
        matches = [
            issue for issue in issues
            if summarise_issue(issue).get("source_id") == "MANIFEST"
        ]
        if len(matches) != 1:
            return {
                "tool": "get_manifest",
                "error": f"Expected one scoped manifest, found {len(matches)}",
            }
        issue = matches[0]
        issue_key = issue.get("key")
        if not issue_key:
            return {
                "tool": "get_manifest",
                "error": "The scoped manifest search result did not include an issue key.",
            }
        comments = client.get_comments(issue_key)
        description = extract_text_from_adf(issue.get("fields", {}).get("description"))
        target_match = re.search(r"Target:\s*(\{[^\n]+\})", description)
        manifest_target = None
        if target_match:
            try:
                manifest_target = json.loads(target_match.group(1))
            except json.JSONDecodeError:
                logger.warning("Manifest target JSON is malformed on %s", issue_key)
        return {
            "tool": "get_manifest",
            "issue": summarise_issue(issue),
            "manifest_target": manifest_target,
            "comments": [summarise_comment(comment) for comment in comments],
        }
    except JiraClientError as exc:
        return {"tool": "get_manifest", "error": str(exc)}
