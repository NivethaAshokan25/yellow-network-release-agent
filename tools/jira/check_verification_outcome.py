"""Check recorded verification outcome for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_verification_outcome as evaluate_verification_outcome
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_verification_outcome",
    "Check the recorded QA result in one scoped Jira evidence record.",
)


def check_verification_outcome(
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict,
):
    return run_evidence_check(
        "check_verification_outcome",
        lambda text, _target: evaluate_verification_outcome(text),
        client,
        project_key,
        label,
        target,
        arguments,
    )
