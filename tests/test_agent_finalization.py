from __future__ import annotations

from agent.agent import (
    AssessmentTrace,
    ToolCallRecord,
    _finalize_assessment,
)


def _trace():
    return AssessmentTrace(
        project_key="TYN",
        label="scope",
        release="R2.4",
        commit="b7a91f2",
        service="atlas-assistant",
        environment="production",
        prompt_version="p7",
        model="qwen3:4b",
        started_at=1.0,
    )


def _manifest_call():
    return ToolCallRecord(
        call_index=0,
        tool_name="get_manifest",
        tool_args={},
        result={
            "issue": {"key": "TYN-1"},
            "manifest_target": {
                "release": "R2.4",
                "commit": "b7a91f2",
                "service": "atlas-assistant",
                "environment": "production",
                "prompt": "p7",
            },
        },
    )


def _requirement(key, req_id, summary, criterion, mandatory=True):
    return {
        "key": key,
        "source_id": req_id,
        "summary": summary,
        "mandatory": mandatory,
        "description": (
            f"Requirement ID: {req_id}\n"
            f"Mandatory: {'yes' if mandatory else 'no'}\n"
            f"Acceptance criterion: {criterion}"
        ),
    }


def _check(call_index, tool_name, req_id, source_ref, result):
    return ToolCallRecord(
        call_index=call_index,
        tool_name=tool_name,
        tool_args={"requirement_id": req_id},
        result={"source_ref": source_ref, **result},
    )


def _scoped_evidence_calls(req_id, source_ref, start_index=1, include_prompt=False, outcome="PASS"):
    calls = [
        _check(
            start_index + index,
            tool_name,
            req_id,
            source_ref,
            {"dimension": dimension, "applicable": True, "reason": "target match"},
        )
        for index, (tool_name, dimension) in enumerate(
            (
                ("check_release", "release"),
                ("check_commit", "commit"),
                ("check_service", "service"),
                ("check_environment", "environment"),
            )
        )
    ]
    next_index = start_index + len(calls)
    if include_prompt:
        calls.append(
            _check(
                next_index,
                "check_prompt_version",
                req_id,
                source_ref,
                {"dimension": "prompt_version", "applicable": True, "reason": "target match"},
            )
        )
        next_index += 1
    calls.append(
        _check(
            next_index,
            "check_verification_outcome",
            req_id,
            source_ref,
            {"applicable": True, "outcome": outcome, "reason": f"verification {outcome}"},
        )
    )
    return calls


def test_finalizer_uses_discovered_requirements_and_enforces_aggregate():
    trace = _trace()
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "First", "All regression cases pass."),
                    _requirement("TYN-11", "REQ-02", "Second", "All isolation cases pass."),
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", "TYN-20#comment-1", start_index=2),
        *_scoped_evidence_calls("REQ-02", "TYN-21#comment-2", start_index=7),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": ["TYN-20#comment-1"],
                },
                {
                    "req_id": "REQ-02",
                    "verdict": "FAIL",
                    "evidence_refs": ["TYN-21#comment-2"],
                },
            ],
            "overall_verdict": "READY",
        },
    )

    assert [req.req_id for req in trace.requirements] == ["REQ-01", "REQ-02"]
    assert trace.overall_verdict == "BLOCKED"
    assert all(req.mandatory for req in trace.requirements)


def test_finalizer_marks_requirements_insufficient_without_manifest():
    trace = _trace()
    trace.tool_calls = [
        ToolCallRecord(
            call_index=0,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "First", "All regression cases pass.")
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", "TYN-20#comment-1"),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": ["TYN-20#comment-1"],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert trace.overall_verdict == "NEEDS_REVIEW"
    assert trace.error


def test_finalizer_does_not_accept_model_requirement_without_jira_issue_key():
    trace = _trace()
    trace.tool_calls = [
        ToolCallRecord(
            call_index=0,
            tool_name="search_requirements",
            tool_args={},
            result={"issues": [{"key": None, "source_id": None, "summary": ""}]},
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {"req_id": "REQ-01", "verdict": "PASS", "summary": "Made-up"}
            ],
        },
    )

    assert trace.requirements == []
    assert trace.overall_verdict == "NEEDS_REVIEW"
    assert "not successfully discovered" in trace.error


def test_model_pass_requires_complete_scope_checks_on_cited_evidence():
    trace = _trace()
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "First", "All regression cases pass.")
                ]
            },
        ),
        _check(
            2,
            "check_release",
            "REQ-01",
            "TYN-20#comment-1",
            {"dimension": "release", "applicable": True},
        ),
        _check(
            3,
            "check_verification_outcome",
            "REQ-01",
            "TYN-20#comment-1",
            {"applicable": True, "outcome": "PASS"},
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": ["TYN-20#comment-1"],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert "scope checks" in trace.requirements[0].reasoning


def test_target_scoped_tool_failure_overrides_model_pass():
    trace = _trace()
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "First", "All regression cases pass.")
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", "TYN-20#comment-1", outcome="FAIL"),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": ["TYN-20#comment-1"],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "FAIL"
    assert trace.overall_verdict == "BLOCKED"
    assert "confirmed a failure" in trace.requirements[0].reasoning


def test_scoped_failure_overrides_pass_even_when_model_omits_its_reference():
    trace = _trace()
    cited_ref = "TYN-20#comment-1"
    uncited_failure_ref = "TYN-21#comment-2"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "First", "All regression cases pass.")
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", cited_ref, start_index=2, outcome="PASS"),
        *_scoped_evidence_calls(
            "REQ-01",
            uncited_failure_ref,
            start_index=7,
            outcome="FAIL",
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {"req_id": "REQ-01", "verdict": "PASS", "evidence_refs": [cited_ref]}
            ]
        },
    )

    assert trace.requirements[0].verdict == "FAIL"
    assert uncited_failure_ref in trace.requirements[0].reasoning


def test_prompt_dependent_requirement_requires_prompt_check_on_same_evidence():
    trace = _trace()
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement(
                        "TYN-10",
                        "REQ-03",
                        "Answer quality",
                        "Evaluation must match the deployed prompt p7.",
                    )
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-03", "TYN-20#comment-1"),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-03",
                    "verdict": "PASS",
                    "evidence_refs": ["TYN-20#comment-1"],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert "prompt-version check" in trace.requirements[0].reasoning


def test_named_explicit_approval_requires_actor_role_and_positive_check():
    trace = _trace()
    source_ref = "TYN-20#comment-1"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement(
                        "TYN-10",
                        "REQ-06",
                        "Security approval",
                        "Security Owner Asha Rao must explicitly approve this release.",
                    )
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-06", source_ref),
        _check(
            7,
            "check_approval_actor",
            "REQ-06",
            source_ref,
            {
                "applicable": True,
                "outcome": "PASS",
                "required_actor": "Asha Rao",
                "required_role": "Security Owner",
            },
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {"req_id": "REQ-06", "verdict": "PASS", "evidence_refs": [source_ref]}
            ]
        },
    )

    assert trace.requirements[0].verdict == "PASS"


def test_missing_named_approval_check_forces_insufficient():
    trace = _trace()
    source_ref = "TYN-20#comment-1"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement(
                        "TYN-10",
                        "REQ-06",
                        "Security approval",
                        "Security Owner Asha Rao must explicitly approve this release.",
                    )
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-06", source_ref),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {"req_id": "REQ-06", "verdict": "PASS", "evidence_refs": [source_ref]}
            ]
        },
    )

    assert trace.requirements[0].verdict == "INSUFFICIENT_EVIDENCE"


def test_exposure_requirement_requires_a_usable_jira_defect_status():
    trace = _trace()
    source_ref = "TYN-20#comment-1"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement(
                        "TYN-10",
                        "REQ-02",
                        "Exposure",
                        "No open exposure may remain on the target build.",
                    )
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-02", source_ref),
        _check(
            7,
            "check_open_defect",
            "REQ-02",
            source_ref,
            {"applicable": False, "outcome": "INSUFFICIENT_EVIDENCE"},
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {"req_id": "REQ-02", "verdict": "PASS", "evidence_refs": [source_ref]}
            ]
        },
    )

    assert trace.requirements[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert "usable open/resolved status" in trace.requirements[0].reasoning


def test_valid_chronological_supersession_with_passing_retest_clears_old_failure():
    trace = _trace()
    earlier_ref = "TYN-20#comment-1"
    later_ref = "TYN-20#comment-2"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "Regression", "All regression cases pass.")
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", earlier_ref, start_index=2, outcome="FAIL"),
        *_scoped_evidence_calls("REQ-01", later_ref, start_index=7, outcome="PASS"),
        ToolCallRecord(
            call_index=12,
            tool_name="check_supersession",
            tool_args={"requirement_id": "REQ-01"},
            result={
                "source_ref": later_ref,
                "earlier_ref": earlier_ref,
                "later_ref": later_ref,
                "applicable": True,
                "chronology_valid": True,
            },
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": [earlier_ref, later_ref],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "PASS"
    assert trace.overall_verdict == "READY"


def test_supersession_without_passing_retest_does_not_clear_failure():
    trace = _trace()
    earlier_ref = "TYN-20#comment-1"
    later_ref = "TYN-20#comment-2"
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement("TYN-10", "REQ-01", "Regression", "All regression cases pass.")
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", earlier_ref, start_index=2, outcome="FAIL"),
        *_scoped_evidence_calls("REQ-01", later_ref, start_index=7, outcome="FAIL"),
        ToolCallRecord(
            call_index=12,
            tool_name="check_supersession",
            tool_args={"requirement_id": "REQ-01"},
            result={
                "source_ref": later_ref,
                "earlier_ref": earlier_ref,
                "later_ref": later_ref,
                "applicable": True,
                "chronology_valid": True,
            },
        ),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "PASS",
                    "evidence_refs": [earlier_ref, later_ref],
                }
            ]
        },
    )

    assert trace.requirements[0].verdict == "FAIL"
    assert trace.overall_verdict == "BLOCKED"


def test_optional_requirement_does_not_block_overall_verdict():
    trace = _trace()
    trace.tool_calls = [
        _manifest_call(),
        ToolCallRecord(
            call_index=1,
            tool_name="search_requirements",
            tool_args={},
            result={
                "issues": [
                    _requirement(
                        "TYN-10",
                        "REQ-01",
                        "Optional item",
                        "A supporting record is available.",
                        mandatory=False,
                    )
                ]
            },
        ),
        *_scoped_evidence_calls("REQ-01", "TYN-20#comment-1"),
    ]

    _finalize_assessment(
        trace,
        {
            "requirements": [
                {
                    "req_id": "REQ-01",
                    "verdict": "INSUFFICIENT_EVIDENCE",
                    "evidence_refs": ["TYN-20#comment-1"],
                }
            ]
        },
    )

    assert trace.requirements[0].mandatory is False
    assert trace.overall_verdict == "READY"
