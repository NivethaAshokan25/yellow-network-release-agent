"""Check a required approver in one scoped Jira evidence record."""
from evaluation.dimensions import Target, check_approval_actor as evaluate_approval_actor
from jira.client import JiraClient
from .common import run_evidence_check
from .tool_schema import evidence_tool_definition

TOOL_DEFINITION = evidence_tool_definition(
    "check_approval_actor",
    "Check whether the required recorded actor and role explicitly approved in one scoped Jira evidence record.",
    {
        "required_actor": {"type": "string", "description": "Required named scenario actor"},
        "required_role": {"type": "string", "description": "Required scenario role"},
    },
    ["required_actor", "required_role"],
)


def check_approval_actor(
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    arguments: dict,
):
    required_actor = str(arguments.get("required_actor", "")).strip()
    required_role = str(arguments.get("required_role", "")).strip()
    if not required_actor or not required_role:
        return {
            "tool": "check_approval_actor",
            "requirement_id": arguments.get("requirement_id", ""),
            "source_ref": arguments.get("issue_key", ""),
            "error": "Both required_actor and required_role are required.",
        }
    return run_evidence_check(
        "check_approval_actor",
        lambda text, _target: evaluate_approval_actor(
            text,
            required_actor,
            required_role,
        ),
        client,
        project_key,
        label,
        target,
        arguments,
        extra={
            "required_actor": required_actor,
            "required_role": required_role,
        },
    )
