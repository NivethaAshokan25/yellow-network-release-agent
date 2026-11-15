"""
Evidence applicability dimensions.

Checks whether a piece of evidence applies to the target context.
These are purely deterministic checks, not AI reasoning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Target:
    release: str
    commit: str
    service: str
    environment: str
    prompt: str


@dataclass
class DimensionResult:
    dimension: str
    applicable: bool
    reason: str
    outcome: Optional[str] = None


def check_release(text: str, target: Target) -> DimensionResult:
    """Evidence must mention the target release."""
    pattern = re.compile(re.escape(target.release), re.IGNORECASE)
    found = bool(pattern.search(text))
    return DimensionResult(
        dimension="release",
        applicable=found,
        reason=f"Target release '{target.release}' {'found' if found else 'NOT found'} in evidence",
    )


def check_commit(text: str, target: Target) -> DimensionResult:
    """Evidence must mention the target commit hash."""
    pattern = re.compile(re.escape(target.commit), re.IGNORECASE)
    found = bool(pattern.search(text))
    return DimensionResult(
        dimension="commit",
        applicable=found,
        reason=f"Target commit '{target.commit}' {'found' if found else 'NOT found'} in evidence",
    )


def check_service(text: str, target: Target) -> DimensionResult:
    """Evidence must mention the target service."""
    pattern = re.compile(re.escape(target.service), re.IGNORECASE)
    found = bool(pattern.search(text))
    return DimensionResult(
        dimension="service",
        applicable=found,
        reason=f"Target service '{target.service}' {'found' if found else 'NOT found'} in evidence",
    )


def check_environment(text: str, target: Target) -> DimensionResult:
    """Evidence must match the target environment."""
    target_pattern = re.compile(
        r"\benvironment\s*[:=]?\s*" + re.escape(target.environment) + r"\b",
        re.IGNORECASE,
    )
    other_pattern = re.compile(r"\benvironment\s*[:=]?\s*([\w-]+)\b", re.IGNORECASE)
    if target_pattern.search(text):
        return DimensionResult(
            dimension="environment",
            applicable=True,
            reason=f"Evidence scoped to target environment '{target.environment}'",
        )
    other_match = other_pattern.search(text)
    if other_match:
        return DimensionResult(
            dimension="environment",
            applicable=False,
            reason=(
                f"Evidence scoped to '{other_match.group(1)}', not target "
                f"environment '{target.environment}'"
            ),
        )
    return DimensionResult(
        dimension="environment",
        applicable=False,
        reason="No environment scope identified in evidence",
    )


def check_prompt_version(
    text: str, target: Target, required: bool = True
) -> DimensionResult:
    """Evidence must mention the deployed prompt version (for prompt-dependent checks)."""
    if not required:
        return DimensionResult(
            dimension="prompt_version",
            applicable=True,
            reason="Prompt version not required for this check",
        )
    pattern = re.compile(r"\bprompt\s*[:=]?\s*" + re.escape(target.prompt) + r"\b", re.IGNORECASE)
    found = bool(pattern.search(text))
    return DimensionResult(
        dimension="prompt_version",
        applicable=found,
        reason=f"Prompt version '{target.prompt}' {'found' if found else 'NOT found'} in evidence",
    )


def check_verification_outcome(text: str) -> DimensionResult:
    """
    Check if a QA run has a positive outcome.
    'X of Y cases pass' where X == Y counts as PASS.
    Aborted runs or partial failures require further inspection.
    """
    # Look for pass patterns like "44 of 44 cases pass" or "60 of 60 mandatory ... pass"
    full_pass = re.compile(
        r"(\d+)\s+of\s+(\d+)\s+(?:mandatory\s+)?(?:\w+\s+)*cases?\s+pass",
        re.IGNORECASE,
    )
    aborted = re.compile(r"run\s+aborted", re.IGNORECASE)
    fail_explicit = re.compile(r"\bFAIL\b", re.IGNORECASE)

    if aborted.search(text):
        return DimensionResult(
            dimension="verification",
            applicable=False,
            reason="Run was aborted — no completed verification result",
        )

    matches = full_pass.findall(text)
    if matches:
        # Take the last match (most recent in text order)
        passed_str, total_str = matches[-1]
        passed, total = int(passed_str), int(total_str)
        if passed == total:
            return DimensionResult(
                dimension="verification",
                applicable=True,
                outcome="PASS",
                reason=f"All {total} cases pass",
            )
        return DimensionResult(
            dimension="verification",
            applicable=True,
            outcome="FAIL",
            reason=f"Only {passed} of {total} cases pass — partial failure",
        )
    if fail_explicit.search(text):
        return DimensionResult(
            dimension="verification",
            applicable=True,
            outcome="FAIL",
            reason="Explicit FAIL found in evidence text",
        )

    return DimensionResult(
        dimension="verification",
        applicable=False,
        reason="No completed verification result found",
    )


def check_approval_actor(
    text: str,
    required_actor: Optional[str],
    required_role: Optional[str] = None,
) -> DimensionResult:
    """
    Check that the required scenario actor and role explicitly approved.
    """
    if not required_actor:
        return DimensionResult(
            dimension="approval",
            applicable=False,
            reason="A required approver was not specified",
        )
    if required_role is not None and not required_role.strip():
        return DimensionResult(
            dimension="approval",
            applicable=False,
            reason="A required approver role was not specified",
        )

    event = re.search(
        r"\bactor\s+([^|\r\n]+)\s*\|\s*role\s+([^\r\n]+)",
        text,
        re.IGNORECASE,
    )
    if event:
        actor_found = event.group(1).strip().casefold() == required_actor.strip().casefold()
        role_text = event.group(2).strip()
        approval_text = text[event.end():]
    else:
        actor_found = required_actor.casefold() in text.casefold()
        role_text = text
        approval_text = text
    role_found = (
        required_role is None
        or required_role.strip().casefold() == role_text.strip().casefold()
    )
    if not actor_found or not role_found:
        missing = []
        if not actor_found:
            missing.append(f"required approver '{required_actor}' is not the recorded actor")
        if not role_found:
            missing.append(f"required role '{required_role}' is not recorded")
        return DimensionResult(
            dimension="approval",
            applicable=False,
            reason="; ".join(missing),
        )

    rejection = re.search(
        r"\b(?:reject(?:ed)?|denied|declined|revoked|"
        r"not\s+(?:(?:explicitly|formally)\s+)?approved|"
        r"did\s+not\s+approve|didn['’]t\s+approve)\b",
        approval_text,
        re.IGNORECASE,
    )
    approval = re.search(
        r"\b(?:approved|approves|approve|signed\s+off|sign-off\s+granted|approval\s+granted)\b",
        approval_text,
        re.IGNORECASE,
    )
    if rejection:
        return DimensionResult(
            dimension="approval",
            applicable=True,
            outcome="FAIL",
            reason=f"Required approver '{required_actor}' explicitly rejected approval",
        )
    if not approval:
        return DimensionResult(
            dimension="approval",
            applicable=True,
            reason=f"Required approver '{required_actor}' is recorded, but explicit approval is missing",
        )
    return DimensionResult(
        dimension="approval",
        applicable=True,
        outcome="PASS",
        reason=f"Required approver '{required_actor}' with role '{required_role or role_text}' explicitly approved",
    )


def check_supersession(earlier_text: str, later_text: str) -> DimensionResult:
    """
    If later_text explicitly supersedes earlier_text, the later result governs.
    Require an explicit supersession statement that identifies the earlier record.
    """
    earlier_ids = set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", earlier_text, re.IGNORECASE))
    explicit = re.search(r"\bexplicitly\s+supersed(?:es|ed)\b", later_text, re.IGNORECASE)
    negated = re.search(
        r"\b(?:does\s+not|doesn't|did\s+not|never)\s+explicitly\s+supersed",
        later_text,
        re.IGNORECASE,
    )
    found = bool(
        explicit
        and not negated
        and earlier_ids
        and any(re.search(rf"\b{re.escape(issue_id)}\b", later_text, re.IGNORECASE) for issue_id in earlier_ids)
    )
    return DimensionResult(
        dimension="supersession",
        applicable=found,
        reason=(
            "Later run explicitly supersedes the earlier identified record"
            if found
            else "No explicit reference to the earlier record was found in the supersession statement"
        ),
    )


def check_open_defect(
    text: str,
    target: Target,
    status_category: Optional[str] = None,
    status_name: Optional[str] = None,
) -> DimensionResult:
    """
    Check if the evidence text indicates an unresolved defect on the target build.
    Jira status is authoritative for whether the record is still open; supersession
    is evaluated separately against the later evidence.
    """
    fail_pattern = re.compile(r"\bFAIL\b", re.IGNORECASE)
    scope_checks = (
        check_release(text, target),
        check_commit(text, target),
        check_service(text, target),
        check_environment(text, target),
    )
    failed_scope = [check for check in scope_checks if not check.applicable]
    if failed_scope:
        return DimensionResult(
            dimension="defect",
            applicable=False,
            outcome="INSUFFICIENT_EVIDENCE",
            reason="Defect evidence is not fully scoped to the target: "
            + "; ".join(check.reason for check in failed_scope),
        )

    if not fail_pattern.search(text):
        return DimensionResult(
            dimension="defect",
            applicable=False,
            reason="No explicit FAIL found in the target-scoped evidence",
        )

    if (status_category or "").casefold() == "done" or (status_name or "").casefold() in {
        "done",
        "closed",
        "resolved",
        "complete",
        "completed",
        "cancelled",
        "canceled",
    }:
        return DimensionResult(
            dimension="defect",
            applicable=False,
            outcome="PASS",
            reason=f"Failure evidence belongs to a resolved Jira issue ({status_name or status_category})",
        )

    if (status_category or "").casefold() not in {"new", "indeterminate"}:
        return DimensionResult(
            dimension="defect",
            applicable=False,
            outcome="INSUFFICIENT_EVIDENCE",
            reason="Jira does not provide a recognized open/closed status category",
        )

    return DimensionResult(
        dimension="defect",
        applicable=True,
        outcome="FAIL",
        reason=f"Unresolved FAIL on target build in Jira status '{status_name or status_category}'",
    )
