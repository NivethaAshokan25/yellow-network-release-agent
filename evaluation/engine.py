"""
Deterministic evidence validator and decision engine.

Checks applicability dimensions, assigns PASS/FAIL/INSUFFICIENT_EVIDENCE,
and aggregates to READY/BLOCKED/NEEDS_REVIEW.

This module works independently of the AI agent —
it can be used to validate AI-gathered evidence OR for unit testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from evaluation.dimensions import (
    Target,
    DimensionResult,
    check_release,
    check_commit,
    check_service,
    check_environment,
    check_prompt_version,
    check_verification_outcome,
    check_approval_actor,
    check_open_defect,
)

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

AGGREGATE_READY = "READY"
AGGREGATE_BLOCKED = "BLOCKED"
AGGREGATE_NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class EvidenceCheck:
    """Result of evaluating a single piece of evidence text."""
    source_ref: str  # e.g. Jira issue key or comment ID
    evidence_text: str
    dimensions: List[DimensionResult] = field(default_factory=list)
    verdict: str = VERDICT_INSUFFICIENT
    reasoning: str = ""


@dataclass
class RequirementVerdict:
    req_id: str
    summary: str
    mandatory: bool
    evidence_checks: List[EvidenceCheck] = field(default_factory=list)
    verdict: str = VERDICT_INSUFFICIENT
    reasoning: str = ""
    jira_refs: List[str] = field(default_factory=list)
    outstanding: str = ""


@dataclass
class AggregateVerdict:
    overall: str
    requirements: List[RequirementVerdict]
    reasoning: str
    blocked_by: List[str] = field(default_factory=list)
    insufficient_items: List[str] = field(default_factory=list)


def evaluate_evidence_text(
    text: str,
    source_ref: str,
    target: Target,
    check_prompt: bool = False,
    required_approver: Optional[str] = None,
) -> EvidenceCheck:
    """
    Apply all relevant dimension checks to an evidence text block.
    Returns an EvidenceCheck with per-dimension results and an overall verdict.
    """
    check = EvidenceCheck(source_ref=source_ref, evidence_text=text[:500])
    dims: List[DimensionResult] = []

    # Mandatory dimensions
    dims.append(check_release(text, target))
    dims.append(check_commit(text, target))
    dims.append(check_service(text, target))
    dims.append(check_environment(text, target))

    if check_prompt:
        dims.append(check_prompt_version(text, target, required=True))

    # Check for open defects
    defect_dim = check_open_defect(text, target)
    dims.append(defect_dim)

    # Verification outcome
    verify_dim = check_verification_outcome(text)
    dims.append(verify_dim)

    # Approver check
    if required_approver:
        dims.append(check_approval_actor(text, required_approver))

    check.dimensions = dims

    # Determine verdict from dimension results
    has_defect = defect_dim.applicable  # True means defect IS present
    all_scope_ok = (
        dims[0].applicable  # release
        and dims[1].applicable  # commit
        and dims[2].applicable  # service
        and dims[3].applicable  # environment
    )

    if has_defect and all_scope_ok:
        check.verdict = VERDICT_FAIL
        check.reasoning = f"Unresolved defect on target build: {defect_dim.reason}"
        return check

    if not all_scope_ok:
        failing = [d for d in dims[:4] if not d.applicable]
        check.verdict = VERDICT_INSUFFICIENT
        check.reasoning = "Evidence not applicable to target scope: " + "; ".join(d.reason for d in failing)
        return check

    if check_prompt and not dims[4].applicable:
        check.verdict = VERDICT_INSUFFICIENT
        check.reasoning = f"Prompt version mismatch: {dims[4].reason}"
        return check

    if required_approver:
        approval_dim = next((d for d in dims if d.dimension == "approval"), None)
        if approval_dim and not approval_dim.applicable:
            check.verdict = VERDICT_INSUFFICIENT
            check.reasoning = f"Required approver not confirmed: {approval_dim.reason}"
            return check

    if verify_dim.outcome == VERDICT_FAIL:
        check.verdict = VERDICT_FAIL
        check.reasoning = f"Applicable verification establishes a failure: {verify_dim.reason}"
    elif defect_dim.outcome == VERDICT_INSUFFICIENT:
        check.verdict = VERDICT_INSUFFICIENT
        check.reasoning = (
            "Target-scoped failure evidence exists, but Jira issue status is unavailable "
            "so its open/resolved state cannot be confirmed."
        )
    elif verify_dim.outcome == VERDICT_PASS:
        check.verdict = VERDICT_PASS
        check.reasoning = f"Applicable affirmative evidence: {verify_dim.reason}"
    else:
        check.verdict = VERDICT_INSUFFICIENT
        check.reasoning = f"Evidence scope matches but verification incomplete: {verify_dim.reason}"

    return check


def aggregate_requirement_verdict(
    req_id: str,
    summary: str,
    checks: List[EvidenceCheck],
    jira_refs: List[str],
) -> RequirementVerdict:
    """
    Given a list of evidence checks for a requirement, determine the final verdict.

    Rules:
    - Any FAIL → FAIL
    - No FAIL + any PASS → PASS (taking the best applicable evidence)
    - No FAIL + no PASS → INSUFFICIENT_EVIDENCE
    """
    rv = RequirementVerdict(
        req_id=req_id,
        summary=summary,
        mandatory=True,
        evidence_checks=checks,
        jira_refs=jira_refs,
    )

    if not checks:
        rv.verdict = VERDICT_INSUFFICIENT
        rv.reasoning = "No evidence records found for this requirement"
        rv.outstanding = "Collect applicable evidence from Jira"
        return rv

    fails = [c for c in checks if c.verdict == VERDICT_FAIL]
    passes = [c for c in checks if c.verdict == VERDICT_PASS]

    if fails:
        rv.verdict = VERDICT_FAIL
        rv.reasoning = "FAIL: " + "; ".join(c.reasoning for c in fails)
        rv.outstanding = "Resolve failure before release"
        return rv

    if passes:
        rv.verdict = VERDICT_PASS
        rv.reasoning = "PASS: " + "; ".join(c.reasoning for c in passes)
        rv.outstanding = ""
        return rv

    # All checks were INSUFFICIENT
    rv.verdict = VERDICT_INSUFFICIENT
    insufficient_reasons = "; ".join(c.reasoning for c in checks)
    rv.reasoning = f"INSUFFICIENT_EVIDENCE: {insufficient_reasons}"
    rv.outstanding = "Missing or out-of-scope evidence for target release/commit/environment"
    return rv


def aggregate_overall_verdict(requirements: List[RequirementVerdict]) -> AggregateVerdict:
    """
    Aggregate all requirement verdicts into a release-level verdict.

    Rules (from manifest):
    - ANY FAIL → BLOCKED
    - No FAIL, any INSUFFICIENT → NEEDS_REVIEW
    - All PASS → READY
    """
    mandatory_requirements = [r for r in requirements if r.mandatory]
    fails = [r for r in mandatory_requirements if r.verdict == VERDICT_FAIL]
    insufficient = [
        r for r in mandatory_requirements
        if r.verdict == VERDICT_INSUFFICIENT
    ]
    passes = [r for r in mandatory_requirements if r.verdict == VERDICT_PASS]

    if fails:
        overall = AGGREGATE_BLOCKED
        reasoning = (
            f"Release is BLOCKED. {len(fails)} requirement(s) have confirmed failures: "
            + ", ".join(r.req_id for r in fails)
        )
        blocked_by = [r.req_id for r in fails]
    elif insufficient:
        overall = AGGREGATE_NEEDS_REVIEW
        reasoning = (
            f"Release NEEDS_REVIEW. No failures, but {len(insufficient)} requirement(s) "
            f"lack sufficient evidence: "
            + ", ".join(r.req_id for r in insufficient)
        )
        blocked_by = []
    else:
        overall = AGGREGATE_READY
        reasoning = f"All {len(passes)} mandatory requirements have passing evidence. Release is READY."
        blocked_by = []

    return AggregateVerdict(
        overall=overall,
        requirements=requirements,
        reasoning=reasoning,
        blocked_by=blocked_by,
        insufficient_items=[r.req_id for r in insufficient],
    )
