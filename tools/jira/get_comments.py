"""Read comments for an issue in the selected dataset."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import get_scoped_issue, summarise_comment
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "get_comments",
    "Retrieve comments for a Jira issue in the selected dataset. Comments contain recorded event evidence.",
    {"issue_key": {"type": "string", "description": "Jira issue key"}},
    ["issue_key"],
)


def get_comments(
    client: JiraClient,
    issue_key: str,
    project_key: str,
    label: str,
) -> dict[str, Any]:
    try:
        get_scoped_issue(client, issue_key, project_key, label)
        comments = client.get_comments(issue_key)
        return {
            "tool": "get_comments",
            "issue_key": issue_key,
            "count": len(comments),
            "comments": [summarise_comment(comment) for comment in comments],
        }
    except (JiraClientError, ValueError) as exc:
        return {"tool": "get_comments", "error": str(exc), "issue_key": issue_key}
