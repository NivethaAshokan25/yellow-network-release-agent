"""Registry and dispatcher for the individually defined Jira tool modules."""
from __future__ import annotations

import logging
from typing import Any, Dict

from evaluation.dimensions import Target
from jira.client import JiraClient
from tools.jira import (
    TOOL_MODULES,
    check_approval_actor,
    check_commit,
    check_environment,
    check_open_defect,
    check_prompt_version,
    check_release,
    check_service,
    check_supersession,
    check_verification_outcome,
    get_comments,
    get_issue,
    get_linked_issues,
    get_manifest,
    search_approvals,
    search_defects,
    search_requirements,
    search_verification,
)

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [module.TOOL_DEFINITION for module in TOOL_MODULES]


def dispatch_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
) -> Dict[str, Any]:
    """Route a model-selected tool call to its dedicated Jira module."""
    dispatch = {
        "get_manifest": lambda: get_manifest.get_manifest(client, project_key, label),
        "search_requirements": lambda: search_requirements.search_requirements(
            client, project_key, label, tool_args.get("release", "")
        ),
        "get_issue": lambda: get_issue.get_issue(
            client, tool_args.get("issue_key", ""), project_key, label
        ),
        "get_comments": lambda: get_comments.get_comments(
            client, tool_args.get("issue_key", ""), project_key, label
        ),
        "get_linked_issues": lambda: get_linked_issues.get_linked_issues(
            client, tool_args.get("issue_key", ""), project_key, label
        ),
        "search_verification": lambda: search_verification.search_verification(
            client,
            project_key,
            label,
            tool_args.get("release", ""),
            tool_args.get("service"),
            tool_args.get("environment"),
        ),
        "search_approvals": lambda: search_approvals.search_approvals(
            client,
            project_key,
            label,
            tool_args.get("release", ""),
            tool_args.get("service"),
            tool_args.get("environment"),
        ),
        "search_defects": lambda: search_defects.search_defects(
            client,
            project_key,
            label,
            tool_args.get("release", ""),
            tool_args.get("service"),
            tool_args.get("environment"),
        ),
        "check_release": lambda: check_release.check_release(
            client, project_key, label, target, tool_args
        ),
        "check_commit": lambda: check_commit.check_commit(
            client, project_key, label, target, tool_args
        ),
        "check_service": lambda: check_service.check_service(
            client, project_key, label, target, tool_args
        ),
        "check_environment": lambda: check_environment.check_environment(
            client, project_key, label, target, tool_args
        ),
        "check_prompt_version": lambda: check_prompt_version.check_prompt_version(
            client, project_key, label, target, tool_args
        ),
        "check_verification_outcome": lambda: check_verification_outcome.check_verification_outcome(
            client, project_key, label, target, tool_args
        ),
        "check_approval_actor": lambda: check_approval_actor.check_approval_actor(
            client, project_key, label, target, tool_args
        ),
        "check_open_defect": lambda: check_open_defect.check_open_defect(
            client, project_key, label, target, tool_args
        ),
        "check_supersession": lambda: check_supersession.check_supersession(
            client, project_key, label, tool_args
        ),
    }
    fn = dispatch.get(tool_name)
    if not fn:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return fn()
    except Exception as exc:
        logger.error("Tool %s raised: %s", tool_name, exc, exc_info=True)
        return {"tool": tool_name, "error": str(exc)}
