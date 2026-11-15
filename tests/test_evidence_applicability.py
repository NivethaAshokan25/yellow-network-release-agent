"""
Tests for evidence applicability dimension checks.
No Jira credentials or Ollama required.
"""
from __future__ import annotations

from evaluation.dimensions import (
    Target,
    check_release,
    check_commit,
    check_service,
    check_environment,
    check_prompt_version,
    check_verification_outcome,
    check_approval_actor,
    check_supersession,
    check_open_defect,
)
from evaluation.engine import evaluate_evidence_text


TARGET = Target(
    release="R2.4",
    commit="b7a91f2",
    service="atlas-assistant",
    environment="production",
    prompt="p7",
)

# Evidence text from dataset (realistic)
GOOD_EVIDENCE = (
    "Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead\n"
    "EVAL-55 completed: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production. Answer accuracy 96.2 percent across 500 graded questions. Approved."
)

WRONG_RELEASE = (
    "Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead\n"
    "run CHK-100: release R2.3; commit b7a91f2; service atlas-assistant; "
    "environment production. 50 of 50 cases pass."
)

WRONG_COMMIT = (
    "Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead\n"
    "run CHK-100: release R2.4; commit deadbeef; service atlas-assistant; "
    "environment production. 50 of 50 cases pass."
)

WRONG_SERVICE = (
    "run CHK-100: release R2.4; commit b7a91f2; service support-widget; "
    "environment production. 50 of 50 cases pass."
)

STAGING_EVIDENCE = (
    "run CHK-100: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment staging. 50 of 50 cases pass."
)

ABORTED_EVIDENCE = (
    "run CHK-266: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production; prompt p7. Run aborted; rerun scheduled."
)

PARTIAL_PASS = (
    "run CHK-100: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production. 45 of 50 cases pass."
)

FAIL_EVIDENCE = (
    "REDACT-870: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production. Customer phone numbers found unredacted. FAIL."
)

SUPERSEDED_FAIL = (
    "REDACT-870: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production. FAIL.\n"
    "REDACT-901 explicitly supersedes REDACT-870. Retest on release R2.4; "
    "commit b7a91f2; service atlas-assistant; environment production: 28 of 28 cases pass."
)

WRONG_PROMPT = (
    "run CHK-100: release R2.4; commit b7a91f2; service atlas-assistant; "
    "environment production; prompt p6. 50 of 50 cases pass."
)


# ── Release check ─────────────────────────────────────────────────────────────

def test_check_release_match():
    result = check_release(GOOD_EVIDENCE, TARGET)
    assert result.applicable is True


def test_check_release_no_match():
    result = check_release(WRONG_RELEASE, TARGET)
    # R2.3 ≠ R2.4
    assert result.applicable is False


# ── Commit check ──────────────────────────────────────────────────────────────

def test_check_commit_match():
    result = check_commit(GOOD_EVIDENCE, TARGET)
    assert result.applicable is True


def test_check_commit_no_match():
    result = check_commit(WRONG_COMMIT, TARGET)
    assert result.applicable is False


# ── Service check ─────────────────────────────────────────────────────────────

def test_check_service_match():
    result = check_service(GOOD_EVIDENCE, TARGET)
    assert result.applicable is True


def test_check_service_no_match():
    result = check_service(WRONG_SERVICE, TARGET)
    assert result.applicable is False


# ── Environment check ─────────────────────────────────────────────────────────

def test_check_environment_production():
    result = check_environment(GOOD_EVIDENCE, TARGET)
    assert result.applicable is True


def test_check_environment_staging_rejected():
    result = check_environment(STAGING_EVIDENCE, TARGET)
    assert result.applicable is False
    assert "staging" in result.reason.lower()


def test_check_environment_no_scope():
    result = check_environment("No environment mentioned here", TARGET)
    assert result.applicable is False


# ── Prompt version check ──────────────────────────────────────────────────────

def test_check_prompt_version_match():
    text = "run R2.4 commit b7a91f2 service atlas-assistant environment production prompt p7. 50 of 50 pass."
    result = check_prompt_version(text, TARGET, required=True)
    assert result.applicable is True


def test_check_prompt_version_mismatch():
    result = check_prompt_version(WRONG_PROMPT, TARGET, required=True)
    assert result.applicable is False


def test_check_prompt_version_not_required():
    result = check_prompt_version("any text", TARGET, required=False)
    assert result.applicable is True


# ── Verification outcome check ─────────────────────────────────────────────────

def test_verification_aborted():
    result = check_verification_outcome(ABORTED_EVIDENCE)
    assert result.applicable is False
    assert "aborted" in result.reason.lower()


def test_verification_all_pass():
    text = "50 of 50 cases pass."
    result = check_verification_outcome(text)
    assert result.applicable is True
    assert result.outcome == "PASS"


def test_verification_partial_fail():
    result = check_verification_outcome(PARTIAL_PASS)
    assert result.applicable is True
    assert result.outcome == "FAIL"
    assert "45 of 50" in result.reason


def test_verification_explicit_fail():
    result = check_verification_outcome(FAIL_EVIDENCE)
    assert result.applicable is True
    assert result.outcome == "FAIL"


def test_verification_no_result():
    result = check_verification_outcome("Engineer picked this up.")
    assert result.applicable is False


# ── Approval actor check ──────────────────────────────────────────────────────

def test_approval_actor_found():
    text = (
        "Recorded event 2026-11-08T14:00:00+05:30 | actor Asha Rao | "
        "role Security Owner\nI explicitly approve release R2.4 for production."
    )
    result = check_approval_actor(text, "Asha Rao", "Security Owner")
    assert result.applicable is True
    assert result.outcome == "PASS"


def test_approval_actor_not_found():
    text = (
        "Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | "
        "role QA Lead\nAsha Rao approved the release."
    )
    result = check_approval_actor(text, "Asha Rao", "Security Owner")
    assert result.applicable is False


def test_approval_role_must_match_exactly():
    text = (
        "Recorded event 2026-11-08T14:00:00+05:30 | actor Asha Rao | "
        "role Security Owner Delegate\nI explicitly approve release R2.4."
    )
    result = check_approval_actor(text, "Asha Rao", "Security Owner")
    assert result.applicable is False
    assert "required role" in result.reason


def test_approval_actor_mention_without_explicit_signoff_is_not_pass():
    text = (
        "Recorded event 2026-11-08T14:00:00+05:30 | actor Asha Rao | "
        "role Security Owner\nThe request is ready for approval."
    )
    result = check_approval_actor(text, "Asha Rao", "Security Owner")
    assert result.applicable is True
    assert result.outcome is None


def test_explicit_approval_rejection_is_fail():
    text = (
        "Recorded event 2026-11-08T14:00:00+05:30 | actor Asha Rao | "
        "role Security Owner\nI reject the release approval."
    )
    result = check_approval_actor(text, "Asha Rao", "Security Owner")
    assert result.applicable is True
    assert result.outcome == "FAIL"


def test_approval_no_required_actor():
    result = check_approval_actor("any text", required_actor=None)
    assert result.applicable is False


# ── Supersession check ────────────────────────────────────────────────────────

def test_supersession_detected():
    later = "REDACT-901 explicitly supersedes REDACT-870. All cases pass."
    result = check_supersession("REDACT-870: FAIL", later)
    assert result.applicable is True


def test_no_supersession():
    result = check_supersession(
        "REDACT-870: FAIL",
        "another run with no supersede language",
    )
    assert result.applicable is False


def test_supersession_must_be_explicit_and_name_the_earlier_record():
    result = check_supersession(
        "REDACT-870: FAIL",
        "A later run may supersede an earlier result, but this one does not.",
    )
    assert result.applicable is False


# ── Open defect check ─────────────────────────────────────────────────────────

def test_open_defect_on_target():
    result = check_open_defect(FAIL_EVIDENCE, TARGET, "new", "Open")
    assert result.applicable is True  # True = defect IS present


def test_resolved_issue_is_not_an_open_defect():
    result = check_open_defect(FAIL_EVIDENCE, TARGET, "done", "Done")
    assert result.applicable is False
    assert result.outcome == "PASS"


def test_open_defect_with_unknown_status_is_insufficient():
    result = check_open_defect(FAIL_EVIDENCE, TARGET)
    assert result.applicable is False
    assert result.outcome == "INSUFFICIENT_EVIDENCE"


def test_no_defect_on_clean_evidence():
    result = check_open_defect(GOOD_EVIDENCE, TARGET)
    assert result.applicable is False


# ── Full evaluate_evidence_text ────────────────────────────────────────────────

def test_evaluate_good_evidence_pass():
    text = (
        "run CHK-100: release R2.4; commit b7a91f2; service atlas-assistant; "
        "environment production; prompt p7. 50 of 50 cases pass."
    )
    ec = evaluate_evidence_text(text, "TYN-42", TARGET, check_prompt=True)
    assert ec.verdict == "PASS"


def test_evaluate_wrong_scope_insufficient():
    ec = evaluate_evidence_text(WRONG_RELEASE, "TYN-43", TARGET)
    assert ec.verdict == "INSUFFICIENT_EVIDENCE"


def test_evaluate_staging_evidence_insufficient():
    ec = evaluate_evidence_text(STAGING_EVIDENCE, "TYN-44", TARGET)
    assert ec.verdict == "INSUFFICIENT_EVIDENCE"


def test_evaluate_target_scope_partial_qa_failure_is_fail():
    ec = evaluate_evidence_text(PARTIAL_PASS, "TYN-48", TARGET)
    assert ec.verdict == "FAIL"


def test_evaluate_fail_evidence():
    # Unresolved FAIL on target commit → FAIL verdict
    ec = evaluate_evidence_text(FAIL_EVIDENCE, "TYN-45", TARGET)
    assert ec.verdict == "FAIL"


def test_evaluate_text_only_cannot_claim_supersession_without_jira_status():
    ec = evaluate_evidence_text(SUPERSEDED_FAIL, "TYN-46", TARGET)
    assert ec.verdict == "INSUFFICIENT_EVIDENCE"


def test_evaluate_aborted_run_insufficient():
    ec = evaluate_evidence_text(ABORTED_EVIDENCE, "TYN-47", TARGET)
    assert ec.verdict == "INSUFFICIENT_EVIDENCE"
