"""
AI Assessment Agent.

IMPORTANT: This module NEVER reads inputs/dataset.json.
All evidence is retrieved exclusively from Jira via jira_tools.

Supports Gemini API and Ollama with tool-calling models.
Implements a bounded tool-calling loop with structured traces.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import ollama

from agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from evaluation.dimensions import Target
from evaluation.engine import (
    AGGREGATE_NEEDS_REVIEW,
    RequirementVerdict,
    aggregate_overall_verdict,
)
from agent.gemini import GeminiConversation
from jira.client import JiraClient
from tools.jira_tools import TOOL_DEFINITIONS, dispatch_tool

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 100
CHECK_TOOL_NAMES = {
    "check_release",
    "check_commit",
    "check_service",
    "check_environment",
    "check_prompt_version",
    "check_verification_outcome",
    "check_approval_actor",
    "check_open_defect",
    "check_supersession",
}
SCOPE_CHECK_DIMENSIONS = {
    "check_release": "release",
    "check_commit": "commit",
    "check_service": "service",
    "check_environment": "environment",
}
EVIDENCE_CHECK_NAMES = {
    "check_verification_outcome",
    "check_approval_actor",
    "check_open_defect",
}


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation during assessment."""
    call_index: int
    tool_name: str
    tool_args: Dict[str, Any]
    result: Dict[str, Any]
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class RequirementAssessment:
    req_id: str
    summary: str
    verdict: str  # PASS | FAIL | INSUFFICIENT_EVIDENCE
    reasoning: str
    evidence_refs: List[str] = field(default_factory=list)
    outstanding: str = ""
    mandatory: bool = True


@dataclass
class AssessmentTrace:
    """Full trace of an assessment run."""
    project_key: str
    label: str
    release: str
    commit: str
    service: str
    environment: str
    prompt_version: str
    model: str
    started_at: float
    finished_at: float = 0.0
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    requirements: List[RequirementAssessment] = field(default_factory=list)
    overall_verdict: str = "NEEDS_REVIEW"
    overall_reasoning: str = ""
    raw_llm_response: str = ""
    total_tokens: Optional[int] = None
    provider: str = "ollama"
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at


def _invoke_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    client: JiraClient,
    project_key: str,
    label: str,
    target: Target,
    call_index: int,
) -> ToolCallRecord:
    """Execute a tool and return a structured record."""
    t0 = time.monotonic()
    result = dispatch_tool(tool_name, tool_args, client, project_key, label, target)
    duration_ms = (time.monotonic() - t0) * 1000

    error = result.get("error") if isinstance(result, dict) else None
    return ToolCallRecord(
        call_index=call_index,
        tool_name=tool_name,
        tool_args=tool_args,
        result=result,
        error=error,
        duration_ms=duration_ms,
    )


def _parse_llm_assessment(raw: str) -> Optional[Dict[str, Any]]:
    """Extract JSON assessment block from LLM response."""
    # Try to find a JSON code block
    import re
    pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    m = re.search(pattern, raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try direct JSON parse
    try:
        # Find first '{' and last '}'
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except json.JSONDecodeError:
        pass
    return None


def _normalize_tool_arguments(arguments: Any) -> Dict[str, Any]:
    """Return tool arguments as a validated JSON object."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            raise ValueError("Tool call arguments are not valid JSON") from e
    if not isinstance(arguments, dict):
        raise TypeError("Tool call arguments must be a JSON object")
    return arguments


def _discovered_requirements(trace: AssessmentTrace) -> Dict[str, Dict[str, Any]]:
    discovered = {}
    for call in trace.tool_calls:
        if call.tool_name != "search_requirements" or not isinstance(call.result, dict):
            continue
        for issue in call.result.get("issues", []):
            req_id = issue.get("source_id")
            if req_id and issue.get("key"):
                discovered[req_id] = issue
    return discovered


def _acceptance_criterion(issue: Dict[str, Any]) -> str:
    description = str(issue.get("description", ""))
    match = re.search(
        r"Acceptance criterion:\s*(.*)",
        description,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else description


def _ref_matches(citation: str, source_ref: str) -> bool:
    if citation == source_ref:
        return True
    issue_key = source_ref.split("#comment-", 1)[0]
    return citation == issue_key


def _successful_tool_result(call: ToolCallRecord) -> bool:
    return (
        not call.error
        and isinstance(call.result, dict)
        and not call.result.get("error")
        and isinstance(call.result.get("source_ref"), str)
        and bool(call.result["source_ref"])
    )


def _scope_checked_refs(
    calls: List[ToolCallRecord],
    requirement_id: str,
    prompt_required: bool,
) -> set[str]:
    required_dimensions = set(SCOPE_CHECK_DIMENSIONS.values())
    if prompt_required:
        required_dimensions.add("prompt_version")
    checked: Dict[str, set[str]] = {}
    for call in calls:
        if call.tool_args.get("requirement_id") != requirement_id:
            continue
        dimension = SCOPE_CHECK_DIMENSIONS.get(call.tool_name)
        if call.tool_name == "check_prompt_version" and prompt_required:
            dimension = "prompt_version"
        if (
            dimension
            and _successful_tool_result(call)
            and call.result.get("applicable") is True
        ):
            checked.setdefault(call.result["source_ref"], set()).add(dimension)
    return {
        source_ref
        for source_ref, dimensions in checked.items()
        if required_dimensions.issubset(dimensions)
    }


def _valid_superseded_refs(
    calls: List[ToolCallRecord],
    requirement_id: str,
    scope_checked_refs: set[str],
) -> set[str]:
    superseded = set()
    for call in calls:
        if (
            call.tool_name != "check_supersession"
            or call.tool_args.get("requirement_id") != requirement_id
            or not _successful_tool_result(call)
            or call.result.get("applicable") is not True
            or call.result.get("chronology_valid") is not True
        ):
            continue
        earlier_ref = call.result.get("earlier_ref")
        later_ref = call.result.get("later_ref")
        if not isinstance(earlier_ref, str) or later_ref not in scope_checked_refs:
            continue
        later_passes = any(
            result.tool_name == "check_verification_outcome"
            and result.tool_args.get("requirement_id") == requirement_id
            and _successful_tool_result(result)
            and result.result.get("source_ref") == later_ref
            and result.result.get("outcome") == "PASS"
            for result in calls
        )
        if later_passes:
            superseded.add(earlier_ref)
    return superseded


def _finalize_assessment(trace: AssessmentTrace, parsed: Optional[Dict[str, Any]]) -> None:
    discovered = _discovered_requirements(trace)
    manifest_result = next(
        (
            call.result for call in trace.tool_calls
            if call.tool_name == "get_manifest"
            and isinstance(call.result, dict)
            and isinstance(call.result.get("issue"), dict)
        ),
        None,
    )
    manifest_target = manifest_result.get("manifest_target") if manifest_result else None
    expected_target = {
        "release": trace.release,
        "commit": trace.commit,
        "service": trace.service,
        "environment": trace.environment,
        "prompt": trace.prompt_version,
    }
    manifest_loaded = bool(
        manifest_target
        and all(manifest_target.get(key) == value for key, value in expected_target.items())
    )
    reported = {}
    if parsed:
        for requirement in parsed.get("requirements", []):
            req_id = requirement.get("req_id")
            if req_id:
                reported[req_id] = requirement

    requirement_ids = discovered
    if not discovered and not trace.error:
        trace.error = "Requirements were not successfully discovered from the scoped Jira search."
    for req_id, issue in requirement_ids.items():
        decision = reported.get(req_id, {})
        verdict = str(decision.get("verdict", "INSUFFICIENT_EVIDENCE")).upper()
        if verdict not in {"PASS", "FAIL", "INSUFFICIENT_EVIDENCE"}:
            verdict = "INSUFFICIENT_EVIDENCE"

        reasons = []
        if not manifest_loaded:
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append("The Jira release manifest was not retrieved.")
        if req_id not in reported:
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append("The model did not return a decision for this requirement.")

        issue_ref = issue.get("key") if isinstance(issue, dict) else None
        criterion = _acceptance_criterion(issue)
        prompt_required = bool(re.search(r"\bprompt(?:[- ]version)?\b", criterion, re.IGNORECASE))
        scope_checked_refs = _scope_checked_refs(
            trace.tool_calls,
            req_id,
            prompt_required,
        )
        model_refs = decision.get("evidence_refs", [])
        if isinstance(model_refs, str):
            model_refs = [model_refs]
        model_refs = [ref for ref in model_refs if isinstance(ref, str)]
        cited_scoped_refs = {
            source_ref
            for source_ref in scope_checked_refs
            if any(_ref_matches(citation, source_ref) for citation in model_refs)
        }
        if not model_refs:
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append("The model did not cite a Jira evidence reference.")
        elif not cited_scoped_refs:
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append(
                "No cited evidence has successful scope checks for release, commit, service, and environment"
                + (" and the required prompt-version check." if prompt_required else ".")
            )

        scoped_evidence_check_calls = [
            call for call in trace.tool_calls
            if call.tool_name in EVIDENCE_CHECK_NAMES
            and call.tool_args.get("requirement_id") == req_id
            and _successful_tool_result(call)
            and call.result.get("source_ref") in scope_checked_refs
        ]
        evidence_check_calls = [
            call
            for call in scoped_evidence_check_calls
            if call.result.get("source_ref") in cited_scoped_refs
        ]
        if cited_scoped_refs and not evidence_check_calls:
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append(
                "No verification, approval, or defect check was completed on the cited scoped evidence."
            )

        if re.search(r"\bexplicitly\s+approve\b", criterion, re.IGNORECASE):
            criterion_lower = criterion.casefold()
            valid_approval = any(
                call.tool_name == "check_approval_actor"
                and call.result.get("outcome") == "PASS"
                and call.result.get("source_ref") in cited_scoped_refs
                and bool(str(call.result.get("required_actor", "")).strip())
                and bool(str(call.result.get("required_role", "")).strip())
                and str(call.result.get("required_actor", "")).casefold() in criterion_lower
                and str(call.result.get("required_role", "")).casefold() in criterion_lower
                for call in evidence_check_calls
            )
            if not valid_approval:
                verdict = "INSUFFICIENT_EVIDENCE"
                reasons.append(
                    "The explicitly required approver and role were not confirmed on cited, target-scoped evidence."
                )

        if re.search(r"\b(?:defect|exposure|breach)\b", criterion, re.IGNORECASE):
            valid_defect_check = any(
                call.tool_name == "check_open_defect"
                and call.result.get("outcome") != "INSUFFICIENT_EVIDENCE"
                for call in scoped_evidence_check_calls
            )
            if not valid_defect_check:
                verdict = "INSUFFICIENT_EVIDENCE"
                reasons.append(
                    "The acceptance criterion requires a defect check, but Jira did not provide "
                    "a usable open/resolved status for the cited evidence."
                )

        superseded_refs = _valid_superseded_refs(
            trace.tool_calls,
            req_id,
            scope_checked_refs,
        )
        deterministic_failures = [
            call
            for call in scoped_evidence_check_calls
            if call.result.get("outcome") == "FAIL"
            and call.result.get("source_ref") not in superseded_refs
            and call.result.get("applicable") is True
        ]
        if manifest_loaded and deterministic_failures:
            verdict = "FAIL"
            reasons.append(
                "A successful target-scoped Jira check confirmed a failure: "
                + ", ".join(sorted({
                    call.result.get("source_ref", "")
                    for call in deterministic_failures
                }))
            )

        tool_refs = [
            call.result.get("source_ref")
            for call in trace.tool_calls
            if call.tool_name in CHECK_TOOL_NAMES
            and call.tool_args.get("requirement_id") == req_id
            and call.result.get("source_ref")
        ]
        evidence_refs = list(dict.fromkeys(
            model_refs
            + ([issue_ref] if issue_ref else [])
            + tool_refs
        ))

        reasoning = decision.get("reasoning", "")
        if reasons:
            reasoning = f"{reasoning} {' '.join(reasons)}".strip()
        trace.requirements.append(
            RequirementAssessment(
                req_id=req_id,
                summary=decision.get("summary") or (
                    issue.get("summary", "") if isinstance(issue, dict) else ""
                ),
                verdict=verdict,
                reasoning=reasoning or "Insufficient Jira evidence for a validated decision.",
                evidence_refs=evidence_refs,
                outstanding=decision.get("outstanding", "")
                or ("Complete the missing Jira evidence checks and rerun." if reasons else ""),
                mandatory=issue.get("mandatory") is not False,
            )
        )

    if not trace.requirements:
        trace.overall_verdict = AGGREGATE_NEEDS_REVIEW
        trace.overall_reasoning = "No requirements were successfully discovered from Jira."
        if not trace.error:
            trace.error = "No requirements were discovered from the scoped Jira search."
        return

    if not manifest_loaded and not trace.error:
        trace.error = "The Jira release manifest was missing or did not match the requested target."

    aggregate = aggregate_overall_verdict(
        [
            RequirementVerdict(
                req_id=req.req_id,
                summary=req.summary,
                mandatory=req.mandatory,
                verdict=req.verdict,
                reasoning=req.reasoning,
                jira_refs=req.evidence_refs,
                outstanding=req.outstanding,
            )
            for req in trace.requirements
        ]
    )
    trace.overall_verdict = aggregate.overall
    trace.overall_reasoning = aggregate.reasoning


def run_assessment(
    client: JiraClient,
    project_key: str,
    label: str,
    release: str,
    commit: str,
    service: str,
    environment: str,
    prompt_version: str,
    model: Optional[str] = None,
    ollama_base_url: str = "http://localhost:11434",
    provider: str = "ollama",
    gemini_api_key: Optional[str] = None,
) -> AssessmentTrace:
    """
    Run the AI assessment agent.

    Returns an AssessmentTrace with full tool call history,
    per-requirement verdicts, and overall release readiness.
    """
    provider = provider.strip().lower()
    if provider not in {"ollama", "gemini"}:
        raise ValueError(f"Unsupported model provider: {provider}")
    model = model or (
        "gemini-3.5-flash-lite" if provider == "gemini" else "qwen3:4b"
    )

    trace = AssessmentTrace(
        project_key=project_key,
        label=label,
        release=release,
        commit=commit,
        service=service,
        environment=environment,
        prompt_version=prompt_version,
        model=model,
        started_at=time.time(),
        provider=provider,
    )

    # Build the initial user message
    user_message = USER_PROMPT_TEMPLATE.format(
        project_key=project_key,
        label=label,
        release=release,
        commit=commit,
        service=service,
        environment=environment,
        prompt=prompt_version,
    )
    target = Target(
        release=release,
        commit=commit,
        service=service,
        environment=environment,
        prompt=prompt_version,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    gemini_conversation = None
    if provider == "gemini":
        api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            trace.error = "GEMINI_API_KEY must be set to use the Gemini provider."
            _finalize_assessment(trace, None)
            trace.finished_at = time.time()
            return trace
        gemini_conversation = GeminiConversation(
            api_key=api_key,
            model=model,
            system_instruction=SYSTEM_PROMPT,
            user_prompt=user_message,
            tool_definitions=TOOL_DEFINITIONS,
        )
    else:
        ollama_client = ollama.Client(host=ollama_base_url)

    call_count = 0

    try:
        while call_count < MAX_TOOL_CALLS:
            logger.info("LLM call %d (tool calls so far: %d)", call_count + 1, call_count)
            if provider == "gemini":
                if gemini_conversation is None:
                    raise RuntimeError("Gemini conversation was not initialized.")
                turn = gemini_conversation.generate()
                trace.total_tokens = (trace.total_tokens or 0) + turn.total_tokens
                if not turn.function_calls:
                    trace.raw_llm_response = turn.text
                    logger.info(
                        "Agent finished. Final response length: %d chars",
                        len(trace.raw_llm_response),
                    )
                    break
                requested_calls = [
                    (
                        f"gemini-{call_count + index}",
                        call.name,
                        _normalize_tool_arguments(call.arguments),
                    )
                    for index, call in enumerate(turn.function_calls)
                ]
            else:
                response = ollama_client.chat(
                    model=model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                )
                msg = response.message
                if hasattr(response, "prompt_eval_count"):
                    trace.total_tokens = (
                        (trace.total_tokens or 0)
                        + response.prompt_eval_count
                        + response.eval_count
                    )

                tool_calls_requested = getattr(msg, "tool_calls", None) or []
                if not tool_calls_requested:
                    trace.raw_llm_response = msg.content or ""
                    logger.info(
                        "Agent finished. Final response length: %d chars",
                        len(trace.raw_llm_response),
                    )
                    break

                normalized_tool_calls = [
                    (tc, _normalize_tool_arguments(tc.function.arguments))
                    for tc in tool_calls_requested
                ]
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id if hasattr(tc, "id") else str(index),
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tool_args,
                            },
                        }
                        for index, (tc, tool_args) in enumerate(normalized_tool_calls)
                    ],
                })
                requested_calls = [
                    (
                        tc.id if hasattr(tc, "id") else str(call_count + index),
                        tc.function.name,
                        tool_args,
                    )
                    for index, (tc, tool_args) in enumerate(normalized_tool_calls)
                ]

            for tool_id, tool_name, tool_args in requested_calls:
                if call_count >= MAX_TOOL_CALLS:
                    logger.warning("Tool call limit %d reached; stopping", MAX_TOOL_CALLS)
                    break

                logger.info("Tool call %d: %s(%s)", call_count + 1, tool_name, tool_args)
                record = _invoke_tool(
                    tool_name,
                    tool_args,
                    client,
                    project_key,
                    label,
                    target,
                    call_count,
                )
                trace.tool_calls.append(record)
                call_count += 1

                if provider == "gemini":
                    if gemini_conversation is None:
                        raise RuntimeError("Gemini conversation was not initialized.")
                    gemini_conversation.submit_tool_result(tool_name, record.result)
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": json.dumps(record.result, default=str),
                    })

        else:
            logger.warning("Tool call loop exited at limit (%d calls)", MAX_TOOL_CALLS)
            trace.error = f"Tool call limit reached ({MAX_TOOL_CALLS})"

    except Exception as e:
        logger.error("Agent error: %s", e, exc_info=True)
        trace.error = str(e)
    finally:
        if gemini_conversation:
            gemini_conversation.close()

    parsed = _parse_llm_assessment(trace.raw_llm_response)
    if not parsed and not trace.error:
        logger.warning("Could not parse structured assessment from LLM response")
        trace.error = "Could not parse JSON assessment"
    _finalize_assessment(trace, parsed)
    trace.finished_at = time.time()
    return trace
