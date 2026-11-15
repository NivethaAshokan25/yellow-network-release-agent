"""Check service applicability for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_service as evaluate_service
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_service",
    "Check whether one scoped Jira evidence record matches the target service.",
)


def check_service(client: JiraClient, project_key: str, label: str, target: Target, arguments: dict):
    return run_evidence_check(
        "check_service", evaluate_service, client, project_key, label, target, arguments
    )
