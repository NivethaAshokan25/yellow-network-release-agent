"""
Report generator: produces Markdown and JSON assessment reports.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.agent import AssessmentTrace, MAX_TOOL_CALLS
from evaluation.engine import AggregateVerdict, RequirementVerdict

logger = logging.getLogger(__name__)

VERDICT_EMOJI = {
    "PASS": "✅",
    "FAIL": "❌",
    "INSUFFICIENT_EVIDENCE": "⚠️",
    "READY": "🟢",
    "BLOCKED": "🔴",
    "NEEDS_REVIEW": "🟡",
}


def _verdict_badge(v: str) -> str:
    return f"{VERDICT_EMOJI.get(v, '?')} **{v}**"


def generate_markdown_report(
    trace: AssessmentTrace,
    aggregate: Optional[AggregateVerdict],
    output_path: Path,
) -> str:
    """Generate a Markdown assessment report and write to file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []

    lines.append("# Release Evidence Assessment Report")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Project:** `{trace.project_key}`  ")
    lines.append(f"**Label:** `{trace.label}`  ")
    lines.append(f"**Target Release:** `{trace.release}`  ")
    lines.append(f"**Target Commit:** `{trace.commit}`  ")
    lines.append(f"**Service:** `{trace.service}`  ")
    lines.append(f"**Environment:** `{trace.environment}`  ")
    lines.append(f"**Prompt Version:** `{trace.prompt_version}`  ")
    lines.append(f"**Model:** `{trace.model}`  ")
    lines.append(f"**Provider:** `{trace.provider}`  ")
    lines.append(f"**Runtime:** {trace.duration_seconds:.1f}s  ")
    if trace.total_tokens:
        lines.append(f"**Total Tokens:** {trace.total_tokens}  ")
    lines.append("")

    # Overall verdict
    overall = aggregate.overall if aggregate else trace.overall_verdict
    lines.append(f"## Overall Verdict: {_verdict_badge(overall)}")
    lines.append("")
    if aggregate:
        lines.append(aggregate.reasoning)
    else:
        lines.append(trace.overall_reasoning or "_No aggregate verdict computed._")
    lines.append("")

    # Requirements table
    lines.append("## Requirements")
    lines.append("")
    lines.append("| Req ID | Summary | Verdict | Jira Refs |")
    lines.append("|--------|---------|---------|-----------|")

    if aggregate and aggregate.requirements:
        for rv in aggregate.requirements:
            verdict_cell = _verdict_badge(rv.verdict)
            refs = ", ".join(rv.jira_refs[:5]) if rv.jira_refs else "_none_"
            lines.append(f"| {rv.req_id} | {rv.summary} | {verdict_cell} | {refs} |")
    elif trace.requirements:
        for r in trace.requirements:
            verdict_cell = _verdict_badge(r.verdict)
            refs = ", ".join(r.evidence_refs[:5]) if r.evidence_refs else "_none_"
            lines.append(f"| {r.req_id} | {r.summary} | {verdict_cell} | {refs} |")
    else:
        lines.append("| _none_ | — | — | — |")

    lines.append("")

    # Per-requirement detail
    lines.append("## Requirement Details")
    lines.append("")

    if aggregate and aggregate.requirements:
        for rv in aggregate.requirements:
            lines.append(f"### {rv.req_id} – {rv.summary}")
            lines.append("")
            lines.append(f"**Verdict:** {_verdict_badge(rv.verdict)}")
            lines.append("")
            lines.append(f"**Reasoning:** {rv.reasoning}")
            if rv.outstanding:
                lines.append(f"**Outstanding:** {rv.outstanding}")
            lines.append("")
            if rv.evidence_checks:
                lines.append("**Evidence checks:**")
                for ec in rv.evidence_checks:
                    lines.append(f"- `{ec.source_ref}`: {ec.verdict} — {ec.reasoning}")
            lines.append("")

    elif trace.requirements:
        for r in trace.requirements:
            lines.append(f"### {r.req_id} – {r.summary}")
            lines.append("")
            lines.append(f"**Verdict:** {_verdict_badge(r.verdict)}")
            lines.append(f"**Reasoning:** {r.reasoning}")
            if r.outstanding:
                lines.append(f"**Outstanding:** {r.outstanding}")
            refs = ", ".join(r.evidence_refs) if r.evidence_refs else "_none_"
            lines.append(f"**Evidence Refs:** {refs}")
            lines.append("")

    # Tool call trace
    lines.append("## Investigation Trace")
    lines.append("")
    if trace.tool_calls:
        lines.append(f"Total tool calls: **{len(trace.tool_calls)}** / {MAX_TOOL_CALLS}")
        lines.append("")
        lines.append("| # | Tool | Args | Result Summary |")
        lines.append("|---|------|------|----------------|")
        for tc in trace.tool_calls:
            args_str = json.dumps(tc.tool_args, default=str)[:80]
            result_text = json.dumps(tc.result, default=str)
            result_str = result_text[:240] + ("..." if len(result_text) > 240 else "")
            status = "❌" if tc.error else "✅"
            lines.append(
                f"| {tc.call_index + 1} | `{tc.tool_name}` | `{args_str}` | "
                f"{status} {result_str} ({tc.duration_ms:.0f} ms) |"
            )
    else:
        lines.append("_No tool calls recorded (agent may not have run)._")

    lines.append("")

    if trace.error:
        lines.append(f"> ⚠️ **Agent error:** {trace.error}")
        lines.append("")

    md_content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_content, encoding="utf-8")
    logger.info("Markdown report written to %s", output_path)
    return md_content


def generate_json_report(
    trace: AssessmentTrace,
    aggregate: Optional[AggregateVerdict],
    output_path: Path,
) -> dict:
    """Generate a structured JSON assessment report and write to file."""
    now = datetime.now(timezone.utc).isoformat()

    report = {
        "generated_at": now,
        "assessment": {
            "project_key": trace.project_key,
            "label": trace.label,
            "target": {
                "release": trace.release,
                "commit": trace.commit,
                "service": trace.service,
                "environment": trace.environment,
                "prompt": trace.prompt_version,
            },
            "model": trace.model,
            "provider": trace.provider,
            "runtime_seconds": round(trace.duration_seconds, 2),
            "total_tokens": trace.total_tokens,
        },
        "overall_verdict": aggregate.overall if aggregate else trace.overall_verdict,
        "overall_reasoning": aggregate.reasoning if aggregate else trace.overall_reasoning,
        "requirements": [],
        "tool_call_count": len(trace.tool_calls),
        "tool_calls": [
            {
                "index": tc.call_index,
                "tool": tc.tool_name,
                "args": tc.tool_args,
                "result": tc.result,
                "error": tc.error,
                "duration_ms": round(tc.duration_ms, 1),
            }
            for tc in trace.tool_calls
        ],
        "agent_error": trace.error,
    }

    if aggregate and aggregate.requirements:
        for rv in aggregate.requirements:
            report["requirements"].append({
                "req_id": rv.req_id,
                "summary": rv.summary,
                "mandatory": rv.mandatory,
                "verdict": rv.verdict,
                "reasoning": rv.reasoning,
                "outstanding": rv.outstanding,
                "jira_refs": rv.jira_refs,
                "evidence_checks": [
                    {
                        "source_ref": ec.source_ref,
                        "verdict": ec.verdict,
                        "reasoning": ec.reasoning,
                        "dimensions": [
                            {"dimension": d.dimension, "applicable": d.applicable, "reason": d.reason}
                            for d in ec.dimensions
                        ],
                    }
                    for ec in rv.evidence_checks
                ],
            })
    elif trace.requirements:
        for r in trace.requirements:
            report["requirements"].append({
                "req_id": r.req_id,
                "summary": r.summary,
                "mandatory": r.mandatory,
                "verdict": r.verdict,
                "reasoning": r.reasoning,
                "outstanding": r.outstanding,
                "evidence_refs": r.evidence_refs,
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info("JSON report written to %s", output_path)
    return report
