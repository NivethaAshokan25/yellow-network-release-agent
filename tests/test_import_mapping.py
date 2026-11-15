"""
Tests for dataset loading and import field mapping.
Does not require Jira credentials or Ollama.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from importer.loader import load_dataset
from importer.models import Dataset, DatasetRecord, parse_recorded_event


# ── Fixtures ──────────────────────────────────────────────────────────────────

MINIMAL_DATASET = {
    "dataset_label": "tyn-release-assessment-v2",
    "target": {
        "release": "R2.4",
        "commit": "b7a91f2",
        "prompt": "p7",
        "environment": "production",
        "service": "atlas-assistant",
    },
    "records": [
        {
            "source_id": "MANIFEST",
            "kind": "manifest",
            "summary": "Release manifest for atlas-assistant R2.4",
            "release": "R2.4",
            "recorded_status": "Done",
            "description": "Fictional manifest",
            "comments": ["Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead\nManifest comment"],
            "links": ["REQ-01"],
        },
        {
            "source_id": "REQ-01",
            "kind": "requirement",
            "summary": "Automated regression tests",
            "release": "R2.4",
            "recorded_status": "Done",
            "description": "Requirement ID: REQ-01\nTarget: {\"release\": \"R2.4\", \"commit\": \"b7a91f2\"}\nMandatory: yes\nAcceptance criterion: All automated regression cases must pass.",
            "comments": [],
            "links": ["REC-001"],
        },
        {
            "source_id": "REC-001",
            "kind": "evidence",
            "summary": "Regression tests: atlas-assistant R2.4",
            "release": "R2.4",
            "recorded_status": "Done",
            "description": "Engineering record",
            "comments": [
                "Recorded event 2026-11-07T10:00:00+05:30 | actor Priya Nair | role QA Lead\nRegression run CHK-100: release R2.4; commit b7a91f2; service atlas-assistant; environment production; prompt p7. 50 of 50 cases pass."
            ],
            "links": [],
        },
    ],
    "note": "Test fixture",
}


@pytest.fixture
def dataset_file(tmp_path):
    p = tmp_path / "dataset.json"
    p.write_text(json.dumps(MINIMAL_DATASET), encoding="utf-8")
    return p


@pytest.fixture
def loaded_dataset(dataset_file) -> Dataset:
    return load_dataset(dataset_file)


# ── Load tests ────────────────────────────────────────────────────────────────

def test_load_dataset_success(dataset_file):
    ds = load_dataset(dataset_file)
    assert ds.dataset_label == "tyn-release-assessment-v2"
    assert ds.target.release == "R2.4"
    assert ds.target.commit == "b7a91f2"
    assert ds.target.service == "atlas-assistant"
    assert len(ds.records) == 3


def test_load_dataset_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_dataset("/nonexistent/path/dataset.json")


def test_dataset_target_fields(loaded_dataset):
    t = loaded_dataset.target
    assert t.release == "R2.4"
    assert t.commit == "b7a91f2"
    assert t.prompt == "p7"
    assert t.environment == "production"
    assert t.service == "atlas-assistant"


# ── Record field mapping ──────────────────────────────────────────────────────

def test_record_kinds(loaded_dataset):
    kinds = {r.kind for r in loaded_dataset.records}
    assert "manifest" in kinds
    assert "requirement" in kinds
    assert "evidence" in kinds


def test_record_source_ids(loaded_dataset):
    ids = {r.source_id for r in loaded_dataset.records}
    assert "MANIFEST" in ids
    assert "REQ-01" in ids
    assert "REC-001" in ids


def test_manifest_links(loaded_dataset):
    manifest = loaded_dataset.manifest
    assert manifest is not None
    assert manifest.source_id == "MANIFEST"
    assert "REQ-01" in manifest.links


def test_requirements_list(loaded_dataset):
    reqs = loaded_dataset.requirements
    assert len(reqs) == 1
    assert reqs[0].source_id == "REQ-01"


def test_get_record(loaded_dataset):
    rec = loaded_dataset.get_record("REC-001")
    assert rec is not None
    assert rec.kind == "evidence"
    assert rec.release == "R2.4"


def test_get_record_not_found(loaded_dataset):
    assert loaded_dataset.get_record("NONEXISTENT") is None


def test_record_links(loaded_dataset):
    req = loaded_dataset.get_record("REQ-01")
    assert "REC-001" in req.links


def test_record_comments_preserved(loaded_dataset):
    rec = loaded_dataset.get_record("REC-001")
    assert len(rec.comments) == 1
    assert "b7a91f2" in rec.comments[0]


# ── Comment parsing ────────────────────────────────────────────────────────────

def test_parse_recorded_event_basic():
    text = "Recorded event 2026-11-07T10:00:00+05:30 | actor Priya Nair | role QA Lead\nThis is the body."
    ev = parse_recorded_event(text)
    assert ev is not None
    assert ev.timestamp == "2026-11-07T10:00:00+05:30"
    assert ev.actor == "Priya Nair"
    assert ev.role == "QA Lead"
    assert ev.body == "This is the body."


def test_parse_recorded_event_multiline_body():
    text = "Recorded event 2026-11-07T10:00:00+05:30 | actor Sam | role Dev\nLine 1\nLine 2"
    ev = parse_recorded_event(text)
    assert ev is not None
    assert "Line 1" in ev.body
    assert "Line 2" in ev.body


def test_parse_recorded_event_invalid_returns_none():
    text = "This is not a recorded event at all."
    ev = parse_recorded_event(text)
    assert ev is None


def test_parsed_comments_on_record(loaded_dataset):
    rec = loaded_dataset.get_record("REC-001")
    events = rec.parsed_comments()
    assert len(events) == 1
    assert events[0].actor == "Priya Nair"


def test_parsed_comments_manifest_has_events(loaded_dataset):
    manifest = loaded_dataset.manifest
    events = manifest.parsed_comments()
    assert len(events) == 1
    assert events[0].role == "QA Lead"


# ── Schema validation ──────────────────────────────────────────────────────────

def test_invalid_kind_raises_validation_error():
    bad_dataset = {**MINIMAL_DATASET}
    bad_dataset["records"] = [
        {
            "source_id": "X-1",
            "kind": "invalid_kind",  # not in allowed set
            "summary": "test",
            "release": "R2.4",
            "recorded_status": "Done",
            "description": "desc",
            "comments": [],
            "links": [],
        }
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bad_dataset, f)
        tmp_path = f.name

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        load_dataset(tmp_path)


def test_real_dataset_loads():
    """Smoke test: the real dataset.json loads without errors."""
    real_path = Path("inputs/dataset.json")
    if not real_path.exists():
        pytest.skip("Real dataset.json not available at inputs/dataset.json")
    ds = load_dataset(real_path)
    assert ds.dataset_label == "tyn-release-assessment-v2"
    assert len(ds.records) == 400
    assert len(ds.requirements) == 12
    assert ds.manifest is not None
