"""
Tests for importer idempotency and dry-run behavior.
No Jira credentials required — uses SQLite and mocked client.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db import store
from importer.models import Dataset, DatasetRecord, DatasetTarget


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test_state.db")
    store.init_db(db_path)
    return db_path


@pytest.fixture
def sample_dataset():
    return Dataset(
        dataset_label="custom-scope",
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
                summary="Regression: atlas-assistant R2.4",
                release="R2.4",
                recorded_status="Done",
                description="desc",
                comments=["Recorded event 2026-11-07T10:00:00+05:30 | actor Priya Nair | role QA Lead\nbody"],
                links=[],
            ),
            DatasetRecord(
                source_id="REC-002",
                kind="work",
                summary="Dev work",
                release="R2.4",
                recorded_status="In Progress",
                description="desc2",
                comments=[],
                links=["REC-001"],
            ),
        ],
        note=None,
    )


# ── SQLite state tests ─────────────────────────────────────────────────────────

def test_record_imported_and_retrieve(temp_db):
    store.record_imported("REC-001", "TYN-42", "evidence", "2026-11-07T10:00:00Z", temp_db)
    key = store.get_imported_key("REC-001", temp_db)
    assert key == "TYN-42"


def test_get_imported_key_missing(temp_db):
    key = store.get_imported_key("NONEXISTENT", temp_db)
    assert key is None


def test_record_imported_idempotent(temp_db):
    """Inserting the same source_id twice should not raise (uses INSERT OR REPLACE)."""
    store.record_imported("REC-001", "TYN-42", "evidence", "2026-11-07T10:00:00Z", temp_db)
    store.record_imported("REC-001", "TYN-99", "evidence", "2026-11-08T10:00:00Z", temp_db)
    # Second insert replaces the first
    key = store.get_imported_key("REC-001", temp_db)
    assert key == "TYN-99"


def test_clear_import_state(temp_db):
    store.record_imported("REC-001", "TYN-42", "evidence", "2026-11-07T10:00:00Z", temp_db)
    store.clear_import_state(temp_db)
    key = store.get_imported_key("REC-001", temp_db)
    assert key is None


def test_get_all_imported(temp_db):
    store.record_imported("REC-001", "TYN-42", "evidence", "2026-11-07T10:00:00Z", temp_db)
    store.record_imported("REC-002", "TYN-43", "work", "2026-11-07T10:00:00Z", temp_db)
    rows = store.get_all_imported(temp_db)
    assert len(rows) == 2
    source_ids = {r["source_id"] for r in rows}
    assert source_ids == {"REC-001", "REC-002"}


def test_record_link(temp_db):
    store.record_link("REC-001", "REC-002", temp_db)
    # Should not raise on duplicate
    store.record_link("REC-001", "REC-002", temp_db)


# ── Idempotency logic tests ───────────────────────────────────────────────────

def test_skip_already_imported_record(sample_dataset, temp_db):
    """
    If source_id is already in DB, _create_or_skip_issue should return existing key
    without calling Jira API.
    """
    store.record_imported("REC-001", "TYN-42", "evidence", "2026-11-07T10:00:00Z", temp_db)

    from importer.jira_importer import _create_or_skip_issue

    mock_client = MagicMock()
    result, error = _create_or_skip_issue(
        client=mock_client,
        record=sample_dataset.records[0],
        project_key="TYN",
        dry_run=False,
        db_path=temp_db,
        dataset_label=sample_dataset.dataset_label,
    )
    assert result == "TYN-42"
    assert error is None
    mock_client.create_issue.assert_not_called()


def test_dry_run_no_jira_calls(sample_dataset, temp_db):
    """Dry run should not call any Jira write methods."""
    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        dry_run=True,
        db_path=temp_db,
    )

    mock_client.create_issue.assert_not_called()
    mock_client.add_comment.assert_not_called()
    mock_client.create_issue_link.assert_not_called()


def test_new_record_is_created_and_persisted(sample_dataset, temp_db):
    """A new record (not in DB) should be created in Jira and persisted to DB."""
    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.create_issue.side_effect = [
        {"key": "TYN-100"},
        {"key": "TYN-101"},
    ]
    mock_client.get_link_types.return_value = [{"name": "Relates"}]

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        dry_run=False,
        db_path=temp_db,
    )

    assert len(result.created) == 2
    assert result.comments_added == 1
    assert mock_client.create_issue.call_count == 2
    # Records should now be in DB
    assert store.get_imported_key("REC-001", temp_db) == "TYN-100"
    assert store.get_imported_key("REC-002", temp_db) == "TYN-101"
    first_fields = mock_client.create_issue.call_args_list[0].args[0]
    assert sample_dataset.dataset_label in first_fields["labels"]
    description = first_fields["description"]["content"][0]["content"][0]["text"]
    assert "recorded_status=Done" in description


def test_second_run_skips_existing(sample_dataset, temp_db):
    """Second run with same dataset should skip all already-imported records."""
    # Pre-populate DB
    store.record_imported("REC-001", "TYN-100", "evidence", "2026-11-07T10:00:00Z", temp_db)
    store.record_imported("REC-002", "TYN-101", "work", "2026-11-07T10:00:00Z", temp_db)
    store.record_link("REC-002", "REC-001", temp_db)

    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_link_types.return_value = [{"name": "Relates"}]

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        dry_run=False,
        db_path=temp_db,
    )

    assert len(result.created) == 0
    assert len(result.skipped) == 2
    mock_client.create_issue.assert_not_called()
    mock_client.create_issue_link.assert_not_called()


def test_update_existing_record_updates_fields_and_only_missing_comment(sample_dataset, temp_db):
    from importer.jira_importer import _make_description_adf, run_import

    record = sample_dataset.records[0]
    record.comments = [record.comments[0], record.comments[0]]
    store.record_imported(record.source_id, "TYN-501", record.kind, "now", temp_db)
    current_issue = {
        "fields": {
            "summary": "Old summary",
            "description": "Old description",
            "labels": ["custom-label", "kind-old", "release-R2.3"],
            "status": {"name": "Done"},
        }
    }
    existing_comment = {
        "body": {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": record.comments[0]}],
            }],
        }
    }
    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_issue.return_value = current_issue
    mock_client.get_comments.return_value = [existing_comment]
    mock_client.get_link_types.return_value = []
    dataset = sample_dataset.model_copy(update={"records": [record]})

    result = run_import(
        dataset=dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        update_existing=True,
        db_path=temp_db,
    )

    assert result.updated == [("REC-001", "TYN-501")]
    assert result.comments_added == 1
    assert result.skipped == []
    mock_client.create_issue.assert_not_called()
    mock_client.update_issue.assert_called_once()
    updated_fields = mock_client.update_issue.call_args.args[1]
    assert updated_fields["summary"] == f"[{record.source_id}] {record.summary}"
    assert updated_fields["description"] == _make_description_adf(record)
    assert updated_fields["labels"] == [
        "custom-label",
        sample_dataset.dataset_label,
        "kind-evidence",
        "release-R2.4",
    ]
    mock_client.add_comment.assert_called_once_with("TYN-501", record.comments[0])


def test_upsert_skips_unchanged_issue_and_existing_comment(sample_dataset, temp_db):
    from importer.jira_importer import _make_description_adf, run_import

    record = sample_dataset.records[0]
    store.record_imported(record.source_id, "TYN-502", record.kind, "now", temp_db)
    current_issue = {
        "fields": {
            "summary": f"[{record.source_id}] {record.summary}",
            "description": _make_description_adf(record),
            "labels": [
                sample_dataset.dataset_label,
                "kind-evidence",
                "release-R2.4",
            ],
            "status": {"name": "Done"},
        }
    }
    existing_comment = {
        "body": {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": record.comments[0]}],
            }],
        }
    }
    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_issue.return_value = current_issue
    mock_client.get_comments.return_value = [existing_comment]
    mock_client.get_link_types.return_value = []

    result = run_import(
        dataset=sample_dataset.model_copy(update={"records": [record]}),
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        update_existing=True,
        db_path=temp_db,
    )

    assert result.updated == []
    assert result.skipped == [record.source_id]
    mock_client.update_issue.assert_not_called()
    mock_client.add_comment.assert_not_called()


def test_rerun_transitions_existing_issue_to_recorded_status(sample_dataset, temp_db):
    store.record_imported("REC-001", "TYN-100", "evidence", "now", temp_db)
    sample_dataset.records = [sample_dataset.records[0]]

    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_issue.return_value = {"fields": {"status": {"name": "To Do"}}}
    mock_client.get_transitions.return_value = [
        {"id": "31", "to": {"name": "Done"}},
    ]
    mock_client.get_link_types.return_value = []

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        db_path=temp_db,
    )

    assert result.skipped == ["REC-001"]
    assert result.status_errors == []
    mock_client.transition_issue.assert_called_once_with("TYN-100", "31")
    mock_client.create_issue.assert_not_called()


def test_rerun_reports_unavailable_status_transition(sample_dataset, temp_db):
    store.record_imported("REC-001", "TYN-100", "evidence", "now", temp_db)
    sample_dataset.records = [sample_dataset.records[0]]

    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_issue.return_value = {"fields": {"status": {"name": "To Do"}}}
    mock_client.get_transitions.return_value = [
        {"id": "21", "to": {"name": "In Progress"}},
    ]
    mock_client.get_link_types.return_value = []

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        db_path=temp_db,
    )

    assert len(result.status_errors) == 1
    assert "cannot transition from to do to done" in result.status_errors[0][2].casefold()
    mock_client.transition_issue.assert_not_called()


def test_force_flag_clears_state(sample_dataset, temp_db):
    """--force should clear DB state and re-import everything."""
    store.record_imported("REC-001", "TYN-100", "evidence", "2026-11-07T10:00:00Z", temp_db)

    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.get_myself.return_value = {"accountId": "abc123"}
    mock_client.create_issue.side_effect = [{"key": "TYN-200"}, {"key": "TYN-201"}]
    mock_client.get_link_types.return_value = [{"name": "Relates"}]

    from importer.jira_importer import run_import

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        dry_run=False,
        force=True,
        db_path=temp_db,
    )

    # DB cleared, so both records re-imported
    assert len(result.created) == 2
    assert mock_client.create_issue.call_count == 2


def test_import_uses_dataset_label(sample_dataset, temp_db):
    sample_dataset.dataset_label = "selected-dataset-scope"
    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.create_issue.side_effect = [{"key": "TYN-300"}, {"key": "TYN-301"}]
    mock_client.get_link_types.return_value = [{"name": "Relates"}]

    from importer.jira_importer import run_import

    run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        db_path=temp_db,
    )

    for call in mock_client.create_issue.call_args_list:
        assert "selected-dataset-scope" in call.args[0]["labels"]


def test_comment_errors_are_reported(sample_dataset, temp_db):
    from jira.client import JiraClientError
    from importer.jira_importer import run_import

    mock_client = MagicMock()
    mock_client.get_project.return_value = {"key": "TYN"}
    mock_client.create_issue.side_effect = [{"key": "TYN-400"}, {"key": "TYN-401"}]
    mock_client.add_comment.side_effect = [JiraClientError(403, "permission denied")]
    mock_client.get_link_types.return_value = [{"name": "Relates"}]

    result = run_import(
        dataset=sample_dataset,
        client=mock_client,
        project_key="TYN",
        project_name="Test",
        db_path=temp_db,
    )

    assert result.comment_errors
    assert result.comment_errors[0][0] == "REC-001"
