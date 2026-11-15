"""Read linked Jira issues while enforcing dataset scope."""
from typing import Any

from jira.client import JiraClient, JiraClientError
from .common import get_scoped_issue, is_in_scope, summarise_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "get_linked_issues",
    "Get issues linked to a given issue, returning only records inside the selected project and dataset.",
    {"issue_key": {"type": "string", "description": "Jira issue key"}},
    ["issue_key"],
)


def get_linked_issues(
    client: JiraClient,
    issue_key: str,
    project_key: str,
    label: str,
) -> dict[str, Any]:
    try:
        issue = get_scoped_issue(client, issue_key, project_key, label)
        linked = []
        skipped_out_of_scope = 0
        for link in issue.get("fields", {}).get("issuelinks", []):
            linked_issue = link.get("outwardIssue") or link.get("inwardIssue")
            if not linked_issue:
                continue
            linked_key = linked_issue.get("key")
            if not linked_key:
                continue
            try:
                linked_result = client.get_issue(linked_key)
                if is_in_scope(linked_result, project_key, label):
                    linked.append(summarise_issue(linked_result))
                else:
                    skipped_out_of_scope += 1
            except JiraClientError as exc:
                linked.append({"key": linked_key, "error": str(exc)})
        return {
            "tool": "get_linked_issues",
            "issue_key": issue_key,
            "count": len(linked),
            "linked_issues": linked,
            "skipped_out_of_scope": skipped_out_of_scope,
        }
    except (JiraClientError, ValueError) as exc:
        return {"tool": "get_linked_issues", "error": str(exc), "issue_key": issue_key}
