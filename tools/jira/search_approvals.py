"""Search scoped Jira records for release approvals."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import jql_literal, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "search_approvals",
    "Search for approval evidence for a release and optional service.",
    {
        "release": {"type": "string", "description": "Release version"},
        "service": {"type": "string", "description": "Optional service name"},
        "environment": {"type": "string", "description": "Optional deployment environment"},
    },
    ["release"],
)


def search_approvals(
    client: JiraClient,
    project_key: str,
    label: str,
    release: str,
    service: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    jql = (
        f"project = {jql_literal(project_key)} "
        f"AND labels = {jql_literal(label)} "
        'AND (text ~ "approval" OR text ~ "approved" OR text ~ "sign-off") '
        f"AND text ~ {jql_literal(release)}"
    )
    if service:
        jql += f" AND text ~ {jql_literal(service)}"
    if environment:
        jql += f" AND text ~ {jql_literal(environment)}"
    try:
        issues = client.search_issues_paginated(jql)
        return {
            "tool": "search_approvals",
            "query": jql,
            "count": len(issues),
            "issues": [summarise_issue(issue) for issue in issues],
        }
    except JiraClientError as exc:
        return {"tool": "search_approvals", "error": str(exc)}
