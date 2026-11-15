"""Local API for browsing reports and starting Jira assessments."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from importer.models import Dataset
from jira.client import JiraClient

load_dotenv()

logger = logging.getLogger(__name__)
app = FastAPI(title="Yellow Network Release Evidence Agent")

_REPORT_ID = re.compile(r"^assessment_\d{8}_\d{6}(?:_[a-f0-9]{8})?$")
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(
        default_factory=lambda: os.environ.get("JIRA_PROJECT_KEY", "TYN"),
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
    )
    label: str = Field(default="tyn-release-assessment-v2", min_length=1, max_length=255)
    release: str = Field(default="R2.4", min_length=1, max_length=128)
    commit: str = Field(default="b7a91f2", min_length=1, max_length=128)
    service: str = Field(default="atlas-assistant", min_length=1, max_length=255)
    environment: str = Field(default="production", min_length=1, max_length=128)
    prompt_version: str = Field(default="p7", min_length=1, max_length=128)
    provider: Optional[Literal["gemini", "ollama"]] = None
    model: Optional[str] = Field(default=None, max_length=255)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(
        default_factory=lambda: os.environ.get("JIRA_PROJECT_KEY", "TYN"),
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
    )
    project_name: str = Field(
        default_factory=lambda: os.environ.get(
            "JIRA_PROJECT_NAME",
            "Yellow Network Release Assessment",
        ),
        min_length=1,
        max_length=255,
    )
    dataset: Dataset


def _reports_dir() -> Path:
    return Path(os.environ.get("REPORTS_DIR", "reports"))


def _jira_base_url() -> str:
    return os.environ.get("JIRA_BASE_URL", "").rstrip("/")


def _load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read report file %s", path.name)
        raise HTTPException(
            status_code=500,
            detail=f"Could not read report {path.name}: {exc}",
        ) from exc
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail=f"Report {path.name} is not a JSON object")
    return report


def _require_jira_credentials(provider: str) -> None:
    missing = [
        key
        for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
        if not os.environ.get(key)
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail="Configure Jira credentials in .env before starting an assessment.",
        )
    if provider == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Configure GEMINI_API_KEY in .env before using Gemini.",
        )


def _update_job(job_id: str, **updates: Any) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def _run_assessment_job(job_id: str, request: AssessmentRequest) -> None:
    _update_job(job_id, status="running")
    try:
        from agent.agent import run_assessment
        from reports.generator import generate_json_report, generate_markdown_report

        provider = request.provider or os.environ.get("LLM_PROVIDER", "gemini").lower()
        model = request.model or (
            os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
            if provider == "gemini"
            else os.environ.get("OLLAMA_MODEL", "qwen3:4b")
        )
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

        with JiraClient(
            os.environ["JIRA_BASE_URL"],
            os.environ["JIRA_EMAIL"],
            os.environ["JIRA_API_TOKEN"],
        ) as client:
            trace = run_assessment(
                client=client,
                project_key=request.project,
                label=request.label,
                release=request.release,
                commit=request.commit,
                service=request.service,
                environment=request.environment,
                prompt_version=request.prompt_version,
                model=model,
                ollama_base_url=ollama_url,
                provider=provider,
            )

        reports_dir = _reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_id = (
            "assessment_"
            + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )
        generate_markdown_report(trace, None, reports_dir / f"{report_id}.md")
        generate_json_report(trace, None, reports_dir / f"{report_id}.json")
        _update_job(
            job_id,
            status="completed",
            report_id=report_id,
            warning=trace.error,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.exception("Assessment job %s failed", job_id)
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _import_result_payload(result: Any, dataset: Dataset) -> dict[str, Any]:
    payload = {
        "dataset_label": dataset.dataset_label,
        "records_total": len(dataset.records),
        "created": [
            {"source_id": source_id, "issue_key": issue_key}
            for source_id, issue_key in result.created
        ],
        "updated": [
            {"source_id": source_id, "issue_key": issue_key}
            for source_id, issue_key in result.updated
        ],
        "skipped": result.skipped,
        "failed": [
            {"source_id": source_id, "error": error}
            for source_id, error in result.failed
        ],
        "comments_added": result.comments_added,
        "comment_errors": [
            {"source_id": source_id, "issue_key": issue_key, "error": error}
            for source_id, issue_key, error in result.comment_errors
        ],
        "links_created": [
            {"source_id": source_id, "linked_source_id": linked_source_id}
            for source_id, linked_source_id in result.links_created
        ],
        "link_errors": result.link_errors,
        "status_updates": [
            {"source_id": source_id, "issue_key": issue_key}
            for source_id, issue_key in result.status_updates
        ],
        "status_errors": [
            {"source_id": source_id, "issue_key": issue_key, "error": error}
            for source_id, issue_key, error in result.status_errors
        ],
    }
    payload["change_count"] = (
        len(payload["created"]) + len(payload["updated"]) + len(payload["links_created"])
    )
    return payload


def _run_import_job(job_id: str, request: ImportRequest) -> None:
    _update_job(job_id, status="running", phase="starting", completed=0, total=len(request.dataset.records))
    try:
        from importer.jira_importer import run_import

        def update_progress(phase: str, completed: int, total: int, detail: str) -> None:
            _update_job(
                job_id,
                phase=phase,
                completed=completed,
                total=total,
                detail=detail,
            )

        with JiraClient(
            os.environ["JIRA_BASE_URL"],
            os.environ["JIRA_EMAIL"],
            os.environ["JIRA_API_TOKEN"],
        ) as client:
            result = run_import(
                dataset=request.dataset,
                client=client,
                project_key=request.project,
                project_name=request.project_name,
                update_existing=True,
                db_path=os.environ.get("DB_PATH", "db/state.db"),
                progress_callback=update_progress,
            )

        payload = _import_result_payload(result, request.dataset)
        has_errors = any(
            payload[key]
            for key in ("failed", "comment_errors", "link_errors", "status_errors")
        )
        _update_job(
            job_id,
            status="completed",
            result=payload,
            warning="Some records were not fully imported; review the reported errors."
            if has_errors
            else None,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.exception("Import job %s failed", job_id)
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _start_job(
    kind: str,
    target: Callable[..., None],
    request: BaseModel,
) -> dict[str, str]:
    with _JOBS_LOCK:
        if any(job["status"] in {"queued", "running"} for job in _JOBS.values()):
            raise HTTPException(
                status_code=409,
                detail="Another import or assessment is already running.",
            )
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        worker = threading.Thread(
            target=target,
            args=(job_id, request),
            name=f"{kind}-{job_id[:8]}",
            daemon=True,
        )
        worker.start()
    return {"job_id": job_id, "status": "queued", "kind": kind}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/reports")
def list_reports() -> dict[str, Any]:
    reports = []
    for path in _reports_dir().glob("assessment_*.json"):
        if not _REPORT_ID.fullmatch(path.stem):
            continue
        report = _load_report(path)
        assessment = report.get("assessment") or {}
        reports.append(
            {
                "id": path.stem,
                "generated_at": report.get("generated_at"),
                "assessment": assessment,
                "overall_verdict": report.get("overall_verdict", "NEEDS_REVIEW"),
                "requirements_count": len(report.get("requirements") or []),
                "tool_call_count": report.get("tool_call_count", 0),
                "agent_error": report.get("agent_error"),
            }
        )
    reports.sort(key=lambda item: item.get("generated_at") or "", reverse=True)
    return {"reports": reports, "jira_base_url": _jira_base_url()}


@app.get("/api/reports/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    if not _REPORT_ID.fullmatch(report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    path = _reports_dir() / f"{report_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    report = _load_report(path)
    report["id"] = report_id
    report["jira_base_url"] = _jira_base_url()
    return report


@app.post("/api/assessments", status_code=202)
def start_assessment(request: AssessmentRequest) -> dict[str, str]:
    provider = request.provider or os.environ.get("LLM_PROVIDER", "gemini").lower()
    _require_jira_credentials(provider)
    effective_request = request.model_copy(update={"provider": provider})
    return _start_job("assessment", _run_assessment_job, effective_request)


@app.post("/api/imports", status_code=202)
def start_import(request: ImportRequest) -> dict[str, str]:
    _require_jira_credentials("import")
    if not request.dataset.records:
        raise HTTPException(status_code=422, detail="Dataset has no records to import.")
    source_ids = [record.source_id for record in request.dataset.records]
    duplicates = sorted(
        source_id for source_id, count in Counter(source_ids).items() if count > 1
    )
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=f"Dataset contains duplicate source identifiers: {', '.join(duplicates[:10])}",
        )
    if not request.dataset.dataset_label.strip():
        raise HTTPException(status_code=422, detail="Dataset label cannot be empty.")
    return _start_job("import", _run_import_job, request)


@app.get("/api/jobs/{job_id}")
@app.get("/api/assessments/{job_id}")
def get_assessment_job(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Assessment job not found")
        return dict(job)
