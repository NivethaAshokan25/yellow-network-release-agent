"""Search scoped Jira records for release verification evidence."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import jql_literal, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "search_verification",
    "Search for verification or QA run evidence. Filter by release and optionally service and environment.",
    {
        "release": {"type": "string", "description": "Release version"},
        "service": {"type": "string", "description": "Optional service name"},
        "environment": {"type": "string", "description": "Optional environment"},
    },
    ["release"],
)


def search_verification(
    client: JiraClient,
    project_key: str,
    label: str,
    release: str,
    service: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    text_parts = [jql_literal(release)]
    if service:
        text_parts.append(jql_literal(service))
    if environment:
        text_parts.append(jql_literal(environment))
    text_query = " AND text ~ ".join(text_parts)
    jql = (
        f"project = {jql_literal(project_key)} "
        f"AND labels = {jql_literal(label)} "
        f"AND text ~ {text_query}"
    )
    try:
        issues = client.search_issues_paginated(jql)
        return {
            "tool": "search_verification",
            "query": jql,
            "count": len(issues),
            "issues": [summarise_issue(issue) for issue in issues],
        }
    except JiraClientError as exc:
        return {"tool": "search_verification", "error": str(exc)}
