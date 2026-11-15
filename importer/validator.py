"""Validate imported Jira records against their source dataset."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List

from importer.models import Dataset
from jira.client import JiraClient, JiraClientError
from db import store

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    total_expected: int = 0
    checked: int = 0
    label_missing: List[str] = field(default_factory=list)
    description_mismatch: List[str] = field(default_factory=list)
    comment_mismatch: List[str] = field(default_factory=list)
    link_mismatch: List[str] = field(default_factory=list)
    status_mismatch: List[str] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)
    passed: bool = False

    def summary(self) -> str:
        lines = [
            "Import Validation Report",
            f"  Expected records: {self.total_expected}",
            f"  Checked in Jira: {self.checked}",
            f"  Issues missing or not mapped: {len(self.not_found)}",
            f"  Dataset label missing: {len(self.label_missing)}",
            f"  Source metadata mismatch: {len(self.description_mismatch)}",
            f"  Comment count mismatch: {len(self.comment_mismatch)}",
            f"  Link mismatch: {len(self.link_mismatch)}",
            f"  Jira status mismatch: {len(self.status_mismatch)}",
            f"  Overall: {'PASS' if self.passed else 'FAIL'}",
        ]
        return "\n".join(lines)


def _extract_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        return " ".join(_extract_text(child) for child in value.get("content", []))
    if isinstance(value, list):
        return " ".join(_extract_text(child) for child in value)
    return ""


def validate_import(
    client: JiraClient,
    dataset: Dataset,
    project_key: str,
    db_path: str = "db/state.db",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ValidationReport:
    """Verify source IDs, dataset labels, comment counts, and links against Jira."""
    report = ValidationReport(total_expected=len(dataset.records))
    imported = {
        row["source_id"]: row["jira_key"]
        for row in store.get_all_imported(db_path)
    }

    for index, record in enumerate(dataset.records, start=1):
        jira_key = imported.get(record.source_id)
        if not jira_key:
            report.not_found.append(record.source_id)
            if progress_callback:
                progress_callback(index, report.total_expected, f"No Jira key for {record.source_id}")
            continue

        try:
            issue = client.get_issue(jira_key)
            fields = issue.get("fields", {})
            labels = fields.get("labels", [])
            if dataset.dataset_label not in labels:
                report.label_missing.append(jira_key)

            actual_status = (fields.get("status") or {}).get("name", "")
            if actual_status.casefold() != record.recorded_status.casefold():
                report.status_mismatch.append(jira_key)

            project = fields.get("project", {}).get("key")
            description = _extract_text(fields.get("description"))
            if (
                project and project.upper() != project_key.upper()
            ) or f"[source_id:{record.source_id}]" not in description or (
                f"recorded_status={record.recorded_status}" not in description
            ):
                report.description_mismatch.append(jira_key)

            comments = client.get_comments(jira_key)
            if len(comments) != len(record.comments):
                report.comment_mismatch.append(jira_key)

            actual_link_keys = set()
            for link in fields.get("issuelinks", []):
                for direction in ("inwardIssue", "outwardIssue"):
                    linked_issue = link.get(direction)
                    if linked_issue and linked_issue.get("key"):
                        actual_link_keys.add(linked_issue["key"])
            expected_link_keys = {
                imported.get(source_id)
                for source_id in record.links
                if imported.get(source_id)
            }
            if not expected_link_keys.issubset(actual_link_keys):
                report.link_mismatch.append(jira_key)
            report.checked += 1
        except JiraClientError as e:
            if e.status == 404:
                report.not_found.append(jira_key)
            else:
                raise

        if progress_callback:
            progress_callback(index, report.total_expected, f"Checked {record.source_id}")

    report.passed = not any(
        (
            report.not_found,
            report.label_missing,
            report.description_mismatch,
            report.comment_mismatch,
            report.link_mismatch,
            report.status_mismatch,
        )
    )
    return report
