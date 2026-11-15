from __future__ import annotations

from db import store
from importer.models import Dataset, DatasetRecord, DatasetTarget
from importer.validator import validate_import


def _dataset():
    return Dataset(
        dataset_label="custom-scope",
        target=DatasetTarget(
            release="R2.4",
            commit="abc123",
            prompt="p1",
            environment="production",
            service="svc",
        ),
        records=[
            DatasetRecord(
                source_id="REQ-01",
                kind="requirement",
                summary="Requirement",
                release="R2.4",
                recorded_status="Done",
                description="Requirement description",
                comments=["Recorded event comment"],
                links=["REC-01"],
            ),
            DatasetRecord(
                source_id="REC-01",
                kind="evidence",
                summary="Evidence",
                release="R2.4",
                recorded_status="In Progress",
                description="Evidence description",
                comments=[],
                links=[],
            ),
        ],
    )


class FakeJiraClient:
    def __init__(self, correct=True):
        labels = ["custom-scope"] if correct else ["wrong-scope"]
        self.issues = {
            "TYN-1": {
                "key": "TYN-1",
                "fields": {
                    "project": {"key": "TYN"},
                    "labels": labels,
                    "status": {"name": "Done"},
                    "description": {
                        "type": "doc",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "[source_id:REQ-01] kind=requirement recorded_status=Done",
                            }],
                        }],
                    },
                    "issuelinks": [{"outwardIssue": {"key": "TYN-2"}}],
                },
            },
            "TYN-2": {
                "key": "TYN-2",
                "fields": {
                    "project": {"key": "TYN"},
                    "labels": labels,
                    "status": {"name": "In Progress"},
                    "description": {
                        "type": "doc",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "[source_id:REC-01] kind=evidence recorded_status=In Progress",
                            }],
                        }],
                    },
                    "issuelinks": [{"inwardIssue": {"key": "TYN-1"}}],
                },
            },
        }
        self.correct = correct

    def get_issue(self, issue_key):
        return self.issues[issue_key]

    def get_comments(self, issue_key):
        return [{"id": "c1"}] if issue_key == "TYN-1" and self.correct else []


def test_validator_checks_source_metadata_comments_and_links(tmp_path):
    dataset = _dataset()
    db_path = str(tmp_path / "state.db")
    store.init_db(db_path)
    store.record_imported("REQ-01", "TYN-1", "requirement", "now", db_path)
    store.record_imported("REC-01", "TYN-2", "evidence", "now", db_path)

    report = validate_import(
        FakeJiraClient(),
        dataset=dataset,
        project_key="TYN",
        db_path=db_path,
    )

    assert report.passed
    assert report.checked == 2
    assert report.total_expected == 2


def test_validator_reports_label_mismatch(tmp_path):
    dataset = _dataset()
    db_path = str(tmp_path / "state.db")
    store.init_db(db_path)
    store.record_imported("REQ-01", "TYN-1", "requirement", "now", db_path)
    store.record_imported("REC-01", "TYN-2", "evidence", "now", db_path)

    report = validate_import(
        FakeJiraClient(correct=False),
        dataset=dataset,
        project_key="TYN",
        db_path=db_path,
    )

    assert not report.passed
    assert report.label_missing == ["TYN-1", "TYN-2"]


def test_validator_reports_jira_status_mismatch(tmp_path):
    dataset = _dataset()
    db_path = str(tmp_path / "state.db")
    store.init_db(db_path)
    store.record_imported("REQ-01", "TYN-1", "requirement", "now", db_path)
    store.record_imported("REC-01", "TYN-2", "evidence", "now", db_path)
    client = FakeJiraClient()
    client.issues["TYN-1"]["fields"]["status"]["name"] = "In Progress"

    report = validate_import(
        client,
        dataset=dataset,
        project_key="TYN",
        db_path=db_path,
    )

    assert not report.passed
    assert report.status_mismatch == ["TYN-1"]
