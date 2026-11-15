"""Check environment applicability for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_environment as evaluate_environment
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_environment",
    "Check whether one scoped Jira evidence record matches the target environment.",
)


def check_environment(
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict,
):
    return run_evidence_check(
        "check_environment", evaluate_environment, client, project_key, label, target, arguments
    )
