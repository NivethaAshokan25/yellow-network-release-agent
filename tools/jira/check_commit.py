"""Check commit applicability for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_commit as evaluate_commit
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_commit",
    "Check whether one scoped Jira evidence record matches the target commit.",
)


def check_commit(client: JiraClient, project_key: str, label: str, target: Target, arguments: dict):
    return run_evidence_check(
        "check_commit", evaluate_commit, client, project_key, label, target, arguments
    )
