import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent.agent import AssessmentTrace
from frontend import api


client = TestClient(api.app)


def test_report_history_and_detail(tmp_path, monkeypatch):
    report = {
        "generated_at": "2026-11-15T10:00:00Z",
        "assessment": {"project_key": "TYN", "target": {"release": "R2.4"}},
        "overall_verdict": "NEEDS_REVIEW",
        "requirements": [{"req_id": "REQ-01"}],
        "tool_call_count": 3,
        "agent_error": None,
    }
    (tmp_path / "assessment_20261115_100000.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    (tmp_path / "assessment_not-a-report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net/")

    history = client.get("/api/reports")
    assert history.status_code == 200
    assert history.json()["reports"] == [
        {
            "id": "assessment_20261115_100000",
            "generated_at": "2026-11-15T10:00:00Z",
            "assessment": {"project_key": "TYN", "target": {"release": "R2.4"}},
            "overall_verdict": "NEEDS_REVIEW",
            "requirements_count": 1,
            "tool_call_count": 3,
            "agent_error": None,
        }
    ]
    assert history.json()["jira_base_url"] == "https://example.atlassian.net"

    detail = client.get("/api/reports/assessment_20261115_100000")
    assert detail.status_code == 200
    assert detail.json()["id"] == "assessment_20261115_100000"
    assert detail.json()["jira_base_url"] == "https://example.atlassian.net"
    assert client.get("/api/reports/../../.env").status_code == 404


def test_assessment_job_writes_report(tmp_path, monkeypatch):
    class FakeJiraClient:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_run_assessment(**kwargs):
        return AssessmentTrace(
            project_key=kwargs["project_key"],
            label=kwargs["label"],
            release=kwargs["release"],
            commit=kwargs["commit"],
            service=kwargs["service"],
            environment=kwargs["environment"],
            prompt_version=kwargs["prompt_version"],
            model=kwargs["model"],
            started_at=time.time() - 0.1,
            finished_at=time.time(),
            provider=kwargs["provider"],
            overall_verdict="READY",
        )

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.net")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(api, "JiraClient", FakeJiraClient)
    monkeypatch.setattr("agent.agent.run_assessment", fake_run_assessment)
    with api._JOBS_LOCK:
        api._JOBS.clear()

    response = client.post(
        "/api/assessments",
        json={"project": "TYN", "label": "tyn-release-assessment-v2", "release": "R2.4"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 5
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/assessments/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)

    assert job is not None
    assert job["status"] == "completed", job
    report_response = client.get(f"/api/reports/{job['report_id']}")
    assert report_response.status_code == 200
    assert report_response.json()["overall_verdict"] == "READY"


def test_assessment_requires_server_side_credentials(monkeypatch):
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    response = client.post("/api/assessments", json={"provider": "ollama"})
    assert response.status_code == 503
    assert "Jira credentials" in response.json()["detail"]


def test_import_job_validates_dataset_and_requests_upsert(tmp_path, monkeypatch):
    from importer.models import Dataset, DatasetRecord, DatasetTarget

    class FakeJiraClient:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    dataset = Dataset(
        dataset_label="tyn-release-assessment-v2",
        target=DatasetTarget(
            release="R2.4",
            commit="b7a91f2",
            prompt="p7",
            environment="production",
            service="atlas-assistant",
        ),
        records=[
            DatasetRecord(
                source_id="REC-001",
                kind="evidence",
                summary="Verification record",
                release="R2.4",
                recorded_status="Done",
                description="Verification evidence",
                comments=[],
                links=[],
            )
        ],
    )
    result = SimpleNamespace(
        created=[("REC-001", "TYN-801")],
        updated=[],
        skipped=[],
        failed=[],
        comments_added=0,
        comment_errors=[],
        links_created=[],
        link_errors=[],
        status_updates=[],
        status_errors=[],
    )
    received = {}

    def fake_run_import(**kwargs):
        received.update(kwargs)
        kwargs["progress_callback"]("records", 1, 1, "Imported REC-001")
        return result

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.net")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    monkeypatch.setattr(api, "JiraClient", FakeJiraClient)
    monkeypatch.setattr("importer.jira_importer.run_import", fake_run_import)
    with api._JOBS_LOCK:
        api._JOBS.clear()

    response = client.post(
        "/api/imports",
        json={"project": "TYN", "dataset": dataset.model_dump()},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 5
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)

    assert job is not None
    assert job["status"] == "completed", job
    assert job["kind"] == "import"
    assert job["result"]["dataset_label"] == dataset.dataset_label
    assert job["result"]["created"] == [{"source_id": "REC-001", "issue_key": "TYN-801"}]
    assert received["update_existing"] is True
    assert received["project_key"] == "TYN"
