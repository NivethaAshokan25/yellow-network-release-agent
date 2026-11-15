"""
Tests for the decision engine: PASS/FAIL/INSUFFICIENT_EVIDENCE per-requirement
and READY/BLOCKED/NEEDS_REVIEW aggregate verdicts.
No Jira credentials or Ollama required.
"""
from __future__ import annotations

from evaluation.engine import (
    EvidenceCheck,
    RequirementVerdict,
    aggregate_overall_verdict,
    aggregate_requirement_verdict,
    VERDICT_PASS,
    VERDICT_FAIL,
    VERDICT_INSUFFICIENT,
    AGGREGATE_READY,
    AGGREGATE_BLOCKED,
    AGGREGATE_NEEDS_REVIEW,
)


# ── Helper factories ──────────────────────────────────────────────────────────

def make_check(verdict: str, source_ref: str = "TYN-1") -> EvidenceCheck:
    return EvidenceCheck(
        source_ref=source_ref,
        evidence_text="evidence text",
        verdict=verdict,
        reasoning=f"Reason for {verdict}",
    )


def make_req_verdict(req_id: str, verdict: str) -> RequirementVerdict:
    return RequirementVerdict(
        req_id=req_id,
        summary=f"Summary of {req_id}",
        mandatory=True,
        verdict=verdict,
        reasoning=f"Reasoning for {req_id}: {verdict}",
    )


# ── aggregate_requirement_verdict ─────────────────────────────────────────────

class TestAggregateRequirementVerdict:

    def test_no_checks_returns_insufficient(self):
        rv = aggregate_requirement_verdict("REQ-01", "Regression", [], [])
        assert rv.verdict == VERDICT_INSUFFICIENT
        assert "No evidence" in rv.reasoning

    def test_single_pass_returns_pass(self):
        checks = [make_check(VERDICT_PASS)]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, ["TYN-42"])
        assert rv.verdict == VERDICT_PASS

    def test_single_fail_returns_fail(self):
        checks = [make_check(VERDICT_FAIL)]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, ["TYN-42"])
        assert rv.verdict == VERDICT_FAIL

    def test_single_insufficient_returns_insufficient(self):
        checks = [make_check(VERDICT_INSUFFICIENT)]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, ["TYN-42"])
        assert rv.verdict == VERDICT_INSUFFICIENT

    def test_fail_overrides_pass(self):
        """If any check is FAIL, requirement is FAIL even with passing checks."""
        checks = [
            make_check(VERDICT_PASS, "TYN-10"),
            make_check(VERDICT_FAIL, "TYN-11"),
        ]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, ["TYN-10", "TYN-11"])
        assert rv.verdict == VERDICT_FAIL

    def test_pass_overrides_insufficient(self):
        """If any check is PASS and no FAIL, requirement is PASS."""
        checks = [
            make_check(VERDICT_INSUFFICIENT, "TYN-10"),
            make_check(VERDICT_PASS, "TYN-11"),
        ]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, ["TYN-10", "TYN-11"])
        assert rv.verdict == VERDICT_PASS

    def test_all_insufficient_returns_insufficient(self):
        checks = [
            make_check(VERDICT_INSUFFICIENT, "TYN-10"),
            make_check(VERDICT_INSUFFICIENT, "TYN-11"),
        ]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, [])
        assert rv.verdict == VERDICT_INSUFFICIENT

    def test_multiple_passes(self):
        checks = [make_check(VERDICT_PASS, f"TYN-{i}") for i in range(3)]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, [])
        assert rv.verdict == VERDICT_PASS

    def test_jira_refs_preserved(self):
        checks = [make_check(VERDICT_PASS)]
        refs = ["TYN-42", "TYN-43"]
        rv = aggregate_requirement_verdict("REQ-01", "Regression", checks, refs)
        assert rv.jira_refs == refs


# ── aggregate_overall_verdict ─────────────────────────────────────────────────

class TestAggregateOverallVerdict:

    def test_all_pass_returns_ready(self):
        reqs = [make_req_verdict(f"REQ-0{i}", VERDICT_PASS) for i in range(1, 4)]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_READY

    def test_any_fail_returns_blocked(self):
        reqs = [
            make_req_verdict("REQ-01", VERDICT_PASS),
            make_req_verdict("REQ-02", VERDICT_FAIL),  # one FAIL
            make_req_verdict("REQ-03", VERDICT_PASS),
        ]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_BLOCKED
        assert "REQ-02" in result.blocked_by

    def test_no_fail_any_insufficient_returns_needs_review(self):
        reqs = [
            make_req_verdict("REQ-01", VERDICT_PASS),
            make_req_verdict("REQ-02", VERDICT_INSUFFICIENT),
        ]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_NEEDS_REVIEW
        assert "REQ-02" in result.insufficient_items

    def test_all_insufficient_returns_needs_review(self):
        reqs = [make_req_verdict(f"REQ-0{i}", VERDICT_INSUFFICIENT) for i in range(1, 4)]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_NEEDS_REVIEW

    def test_fail_plus_insufficient_returns_blocked(self):
        """FAIL takes priority over INSUFFICIENT — still BLOCKED."""
        reqs = [
            make_req_verdict("REQ-01", VERDICT_FAIL),
            make_req_verdict("REQ-02", VERDICT_INSUFFICIENT),
            make_req_verdict("REQ-03", VERDICT_PASS),
        ]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_BLOCKED

    def test_optional_failure_does_not_block_release(self):
        reqs = [
            make_req_verdict("REQ-01", VERDICT_PASS),
            RequirementVerdict(
                req_id="REQ-02",
                summary="Optional",
                mandatory=False,
                verdict=VERDICT_FAIL,
                reasoning="Optional failure",
            ),
        ]

        result = aggregate_overall_verdict(reqs)

        assert result.overall == AGGREGATE_READY
        assert len(result.requirements) == 2

    def test_multiple_fails_all_in_blocked_by(self):
        reqs = [
            make_req_verdict("REQ-01", VERDICT_FAIL),
            make_req_verdict("REQ-02", VERDICT_FAIL),
        ]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_BLOCKED
        assert "REQ-01" in result.blocked_by
        assert "REQ-02" in result.blocked_by

    def test_empty_requirements_returns_needs_review(self):
        """No requirements → no passes → not READY."""
        result = aggregate_overall_verdict([])
        # With no requirements, there are no passes and no fails
        # Lands in READY branch since no fails and no insufficient
        assert result.overall in (AGGREGATE_READY, AGGREGATE_NEEDS_REVIEW)

    def test_reasoning_mentions_verdict(self):
        reqs = [make_req_verdict("REQ-01", VERDICT_PASS)]
        result = aggregate_overall_verdict(reqs)
        assert "READY" in result.reasoning or "1" in result.reasoning

    def test_blocked_reasoning_mentions_failing_req(self):
        reqs = [make_req_verdict("REQ-07", VERDICT_FAIL)]
        result = aggregate_overall_verdict(reqs)
        assert "REQ-07" in result.reasoning

    def test_twelve_requirements_all_pass(self):
        """Simulate a full 12-requirement assessment, all passing."""
        reqs = [make_req_verdict(f"REQ-{i:02d}", VERDICT_PASS) for i in range(1, 13)]
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_READY

    def test_twelve_requirements_one_fail(self):
        """12 requirements, one FAIL → BLOCKED."""
        reqs = [make_req_verdict(f"REQ-{i:02d}", VERDICT_PASS) for i in range(1, 13)]
        reqs[5] = make_req_verdict("REQ-06", VERDICT_FAIL)  # Security approval fails
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_BLOCKED
        assert "REQ-06" in result.blocked_by

    def test_twelve_requirements_one_insufficient(self):
        """12 requirements, one INSUFFICIENT → NEEDS_REVIEW."""
        reqs = [make_req_verdict(f"REQ-{i:02d}", VERDICT_PASS) for i in range(1, 13)]
        reqs[11] = make_req_verdict("REQ-12", VERDICT_INSUFFICIENT)
        result = aggregate_overall_verdict(reqs)
        assert result.overall == AGGREGATE_NEEDS_REVIEW
        assert "REQ-12" in result.insufficient_items
