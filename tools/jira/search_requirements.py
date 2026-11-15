"""Search Jira for scoped requirements in the target release."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import jql_literal, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "search_requirements",
    "Search the Jira project for requirement issues scoped to the dataset label. Returns issue keys, summaries, statuses and linked issue keys.",
    {"release": {"type": "string", "description": "Target release, for example R2.4"}},
    ["release"],
)


def search_requirements(
    client: JiraClient,
    project_key: str,
    label: str,
    release: str,
) -> dict[str, Any]:
    if not release:
        return {
            "tool": "search_requirements",
            "error": "A target release is required.",
            "count": 0,
            "issues": [],
        }

    jql = (
        f"project = {jql_literal(project_key)} "
        f"AND labels = {jql_literal(label)} "
        'AND labels = "kind-requirement" '
        f"AND labels = {jql_literal('release-' + release)}"
    )
    try:
        issues = client.search_issues_paginated(jql)
        return {
            "tool": "search_requirements",
            "query": jql,
            "count": len(issues),
            "issues": [summarise_issue(issue) for issue in issues],
        }
    except JiraClientError as exc:
        return {"tool": "search_requirements", "error": str(exc)}
