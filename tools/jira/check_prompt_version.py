"""Check prompt-version applicability for one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_prompt_version as evaluate_prompt_version
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_prompt_version",
    "Check whether one scoped Jira evidence record matches the deployed prompt version.",
)


def check_prompt_version(
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict,
):
    return run_evidence_check(
        "check_prompt_version",
        lambda text, actual_target: evaluate_prompt_version(text, actual_target, required=True),
        client,
        project_key,
        label,
        target,
        arguments,
    )
