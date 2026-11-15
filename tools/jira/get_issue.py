"""Read a single scoped Jira issue."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import get_scoped_issue, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "get_issue",
    "Retrieve full details of a single Jira issue by its key (for example TYN-42).",
    {"issue_key": {"type": "string", "description": "Jira issue key"}},
    ["issue_key"],
)


def get_issue(
    client: JiraClient,
    issue_key: str,
    project_key: str,
    label: str,
) -> dict[str, Any]:
    try:
        issue = get_scoped_issue(client, issue_key, project_key, label)
        return {"tool": "get_issue", "issue": summarise_issue(issue)}
    except (JiraClientError, ValueError) as exc:
        return {"tool": "get_issue", "error": str(exc), "issue_key": issue_key}
