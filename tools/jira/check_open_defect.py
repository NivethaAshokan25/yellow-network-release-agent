"""Check for an unresolved target-commit defect in scoped Jira evidence."""
from evaluation.dimensions import Target, check_open_defect as evaluate_open_defect
from jira.client import JiraClient, JiraClientError
from .common import (
    dimension_result,
    get_scoped_evidence_text,
    get_scoped_issue,
)
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_open_defect",
    "Check one scoped Jira evidence record for an unresolved target-commit failure.",
)


def check_open_defect(
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict,
):
    requirement_id = arguments.get("requirement_id", "")
    issue_key = arguments.get("issue_key", "")
    try:
        issue = get_scoped_issue(client, issue_key, project_key, label)
        evidence, source_ref = get_scoped_evidence_text(
            client,
            issue_key,
            project_key,
            label,
            arguments.get("comment_id"),
        )
        status = (issue.get("fields") or {}).get("status") or {}
        status_category = (status.get("statusCategory") or {}).get("key")
        status_name = status.get("name")
        dimension = evaluate_open_defect(
            evidence,
            target,
            status_category=status_category,
            status_name=status_name,
        )
        return {
            "tool": "check_open_defect",
            "requirement_id": requirement_id,
            **dimension_result(source_ref, dimension),
            "jira_status": status_name,
            "jira_status_category": status_category,
        }
    except (JiraClientError, ValueError) as exc:
        return {
            "tool": "check_open_defect",
            "requirement_id": requirement_id,
            "source_ref": issue_key,
            "error": str(exc),
        }
