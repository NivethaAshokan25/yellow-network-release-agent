"""Check release applicability for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_release as evaluate_release
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_release",
    "Check whether one scoped Jira evidence record matches the target release.",
)


def check_release(client: JiraClient, project_key: str, label: str, target: Target, arguments: dict):
    return run_evidence_check(
        "check_release", evaluate_release, client, project_key, label, target, arguments
    )
