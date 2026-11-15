"""Search scoped Jira records for defects affecting the target release."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import jql_literal, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "search_defects",
    "Search for defect, bug, or failure records affecting a release and optional service.",
    {
        "release": {"type": "string", "description": "Release version"},
        "service": {"type": "string", "description": "Optional service name"},
        "environment": {"type": "string", "description": "Optional deployment environment"},
    },
    ["release"],
)


def search_defects(
    client: JiraClient,
    project_key: str,
    label: str,
    release: str,
    service: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    text_filter = f"text ~ {jql_literal(release)}"
    if service:
        text_filter += f" AND text ~ {jql_literal(service)}"
    if environment:
        text_filter += f" AND text ~ {jql_literal(environment)}"
    jql = (
        f"project = {jql_literal(project_key)} "
        f"AND labels = {jql_literal(label)} "
        'AND (text ~ "defect" OR text ~ "bug" OR text ~ "fail" OR text ~ "incident") '
        f"AND {text_filter}"
    )
    try:
        issues = client.search_issues_paginated(jql)
        return {
            "tool": "search_defects",
            "query": jql,
            "count": len(issues),
            "issues": [summarise_issue(issue) for issue in issues],
        }
    except JiraClientError as exc:
        return {"tool": "search_defects", "error": str(exc)}
