"""
Idempotent Jira importer.

Reads ONLY inputs/dataset.json.
Never read by assessment agent.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from importer.models import Dataset, DatasetRecord
from jira.client import JiraClient, JiraClientError
from db import store

logger = logging.getLogger(__name__)

SOURCE_ID_PREFIX = "[source_id:"

# Map dataset record kind → Jira issue type name (must exist in project)
KIND_TO_ISSUE_TYPE = {
    "evidence": "Task",
    "work": "Task",
    "requirement": "Task",
    "manifest": "Task",
}

class ImportResult:
    def __init__(self):
        self.created: List[Tuple[str, str]] = []   # (source_id, jira_key)
        self.updated: List[Tuple[str, str]] = []   # (source_id, jira_key)
        self.skipped: List[str] = []                # source_ids already present
        self.failed: List[Tuple[str, str]] = []     # (source_id, error)
        self.comment_errors: List[Tuple[str, str, str]] = []
        self.comments_added = 0
        self.link_errors: List[str] = []
        self.links_created: List[Tuple[str, str]] = []
        self.status_errors: List[Tuple[str, str, str]] = []
        self.status_updates: List[Tuple[str, str]] = []


def _extract_adf_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_extract_adf_text(item) for item in value)))
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        children = value.get("content", [])
        separator = "\n" if value.get("type") in {"paragraph", "heading", "listItem"} else ""
        return separator.join(filter(None, (_extract_adf_text(item) for item in children)))
    return ""


def _make_description_adf(record: DatasetRecord) -> dict:
    """Build ADF doc with original record metadata and description text."""
    text = (
        f"[source_id:{record.source_id}] kind={record.kind} "
        f"recorded_status={record.recorded_status}\n\n{record.description}"
    )
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _ensure_project(client: JiraClient, project_key: str, project_name: str, dry_run: bool) -> bool:
    """Create project if absent. Returns True if project exists/created."""
    existing = client.get_project(project_key)
    if existing:
        logger.info("Project %s already exists", project_key)
        return True
    if dry_run:
        logger.info("[DRY-RUN] Would create project %s (%s)", project_key, project_name)
        return True
    logger.info("Creating project %s ...", project_key)
    try:
        # Fetch current user's accountId for lead
        me = client.get_myself()
        lead_id = me.get("accountId")
        client.create_project(project_key, project_name, lead_account_id=lead_id)
        logger.info("Project %s created", project_key)
        return True
    except JiraClientError as e:
        logger.error("Failed to create project %s: %s", project_key, e)
        return False


def _create_or_skip_issue(
    client: JiraClient,
    record: DatasetRecord,
    project_key: str,
    dry_run: bool,
    db_path: str,
    dataset_label: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Create a Jira issue for a record. Returns jira_key or None.
    Idempotent: skips if source_id already in DB.
    """
    existing_key = store.get_imported_key(record.source_id, db_path)
    if existing_key:
        logger.debug("SKIP %s → already imported as %s", record.source_id, existing_key)
        return existing_key, None

    if dry_run:
        logger.info("[DRY-RUN] Would create issue for %s: %s", record.source_id, record.summary)
        return None, None

    fields = {
        "project": {"key": project_key},
        "summary": f"[{record.source_id}] {record.summary}",
        "description": _make_description_adf(record),
        "issuetype": {"name": "Task"},
        "labels": [dataset_label, f"kind-{record.kind}", f"release-{record.release}"],
    }

    try:
        result = client.create_issue(fields)
        jira_key = result["key"]
        now = datetime.now(timezone.utc).isoformat()
        store.record_imported(record.source_id, jira_key, record.kind, now, db_path)
        logger.info("CREATED %s → %s", record.source_id, jira_key)
        return jira_key, None
    except JiraClientError as e:
        logger.error("FAILED to create %s: %s", record.source_id, e)
        return None, str(e)


def _update_existing_issue(
    client: JiraClient,
    record: DatasetRecord,
    jira_key: str,
    db_path: str,
    dataset_label: str,
    dry_run: bool,
) -> Tuple[bool, Optional[str]]:
    """Update importer-managed issue fields and report whether they changed."""
    try:
        issue = client.get_issue(jira_key)
        current_fields = issue.get("fields") or {}
        current_labels = current_fields.get("labels") or []
        managed_labels = [
            label
            for label in current_labels
            if not label.startswith(("kind-", "release-"))
        ]
        desired_labels = list(dict.fromkeys([
            *managed_labels,
            dataset_label,
            f"kind-{record.kind}",
            f"release-{record.release}",
        ]))
        desired_description = _make_description_adf(record)
        desired_text = desired_description["content"][0]["content"][0]["text"]
        updates = {}

        if current_fields.get("summary") != f"[{record.source_id}] {record.summary}":
            updates["summary"] = f"[{record.source_id}] {record.summary}"
        if _extract_adf_text(current_fields.get("description")) != desired_text:
            updates["description"] = desired_description
        if set(current_labels) != set(desired_labels):
            updates["labels"] = desired_labels

        if updates:
            if dry_run:
                logger.info("[DRY-RUN] Would update issue %s for %s", jira_key, record.source_id)
            else:
                client.update_issue(jira_key, updates)
                store.record_imported(
                    record.source_id,
                    jira_key,
                    record.kind,
                    datetime.now(timezone.utc).isoformat(),
                    db_path,
                )
        return bool(updates), None
    except JiraClientError as e:
        logger.error("FAILED to update %s (%s): %s", record.source_id, jira_key, e)
        return False, str(e)


def _add_comments(
    client: JiraClient,
    record: DatasetRecord,
    jira_key: str,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """Add all dataset comments to the Jira issue."""
    if dry_run:
        logger.info("[DRY-RUN] Would add %d comment(s) to %s", len(record.comments), jira_key)
        return [], 0
    errors = []
    added = 0
    for comment_index, comment_text in enumerate(record.comments, start=1):
        try:
            client.add_comment(jira_key, comment_text)
            added += 1
        except JiraClientError as e:
            logger.warning("Comment add failed on %s: %s", jira_key, e)
            errors.append(f"{record.source_id} comment {comment_index}: {e}")
    return errors, added


def _add_missing_comments(
    client: JiraClient,
    record: DatasetRecord,
    jira_key: str,
) -> Tuple[List[str], int]:
    """Append only comment occurrences that are absent from Jira."""
    if not record.comments:
        return [], 0
    try:
        existing_comments = client.get_comments(jira_key)
    except JiraClientError as e:
        return [f"{record.source_id}: could not read existing comments: {e}"], 0

    existing_counts = Counter(
        _extract_adf_text(comment.get("body")).strip()
        for comment in existing_comments
    )
    errors = []
    added = 0
    for index, comment_text in enumerate(record.comments, start=1):
        if existing_counts[comment_text.strip()]:
            existing_counts[comment_text.strip()] -= 1
            continue
        try:
            client.add_comment(jira_key, comment_text)
            added += 1
        except JiraClientError as e:
            errors.append(f"{record.source_id} comment {index}: {e}")
    return errors, added


def _sync_recorded_status(
    client: JiraClient,
    record: DatasetRecord,
    jira_key: str,
    dry_run: bool,
) -> Tuple[Optional[str], bool]:
    """Move the Jira issue to the recorded status when a matching transition exists."""
    if dry_run:
        logger.info(
            "[DRY-RUN] Would set %s status to %s",
            jira_key,
            record.recorded_status,
        )
        return None, False

    try:
        issue = client.get_issue(jira_key)
        current_status = (
            issue.get("fields", {}).get("status", {}).get("name", "")
        )
        if current_status.casefold() == record.recorded_status.casefold():
            return None, False

        transitions = client.get_transitions(jira_key)
        target_transition = next(
            (
                transition
                for transition in transitions
                if transition.get("to", {}).get("name", "").casefold()
                == record.recorded_status.casefold()
            ),
            None,
        )
        if target_transition is None:
            available = sorted({
                transition.get("to", {}).get("name", "")
                for transition in transitions
                if transition.get("to", {}).get("name")
            })
            return (
                f"Cannot transition from {current_status or 'unknown'} to "
                f"{record.recorded_status}; available targets: "
                f"{', '.join(available) or 'none'}"
            ), False

        client.transition_issue(jira_key, str(target_transition["id"]))
        return None, True
    except JiraClientError as e:
        return str(e), False


def run_import(
    dataset: Dataset,
    client: JiraClient,
    project_key: str,
    project_name: str,
    dry_run: bool = False,
    force: bool = False,
    update_existing: bool = False,
    db_path: str = "db/state.db",
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> ImportResult:
    """
    Main import function. Idempotent by default.
    --force clears existing import state before importing.
    update_existing updates records already mapped to Jira rather than skipping them.
    --dry-run performs no writes.
    """
    result = ImportResult()

    store.init_db(db_path)
    if force and not dry_run:
        logger.warning("--force: clearing existing import state")
        store.clear_import_state(db_path)

    # Ensure project exists
    if not dry_run:
        ok = _ensure_project(client, project_key, project_name, dry_run)
        if not ok:
            raise RuntimeError(f"Cannot proceed: project {project_key} unavailable")

    # Phase 1: Create all issues
    source_to_jira: dict[str, str] = {}
    record_total = len(dataset.records)
    if progress_callback:
        progress_callback("records", 0, record_total, f"Project {project_key} is ready")
    for index, record in enumerate(dataset.records, start=1):
        existing = store.get_imported_key(record.source_id, db_path)
        if existing and not force and not update_existing:
            result.skipped.append(record.source_id)
            source_to_jira[record.source_id] = existing
            status_error, _ = _sync_recorded_status(
                client,
                record,
                existing,
                dry_run,
            )
            if status_error:
                result.status_errors.append(
                    (record.source_id, existing, status_error)
                )
            if progress_callback:
                progress_callback(
                    "records",
                    index,
                    record_total,
                    f"Skipped {record.source_id}"
                    if not status_error
                    else f"Status update failed for {record.source_id}",
                )
            continue

        is_existing = bool(existing and not force)
        if is_existing:
            jira_key = existing
            fields_changed, creation_error = _update_existing_issue(
                client,
                record,
                jira_key,
                db_path,
                dataset.dataset_label,
                dry_run,
            )
            if creation_error:
                result.failed.append((record.source_id, creation_error))
                source_to_jira[record.source_id] = jira_key
                if progress_callback:
                    progress_callback(
                        "records",
                        index,
                        record_total,
                        f"Failed to update {record.source_id}",
                    )
                continue
        else:
            jira_key, creation_error = _create_or_skip_issue(
                client,
                record,
                project_key,
                dry_run,
                db_path,
                dataset.dataset_label,
            )
            fields_changed = bool(jira_key and not dry_run)

        if jira_key:
            source_to_jira[record.source_id] = jira_key
            if not dry_run:
                status_error, status_changed = _sync_recorded_status(
                    client,
                    record,
                    jira_key,
                    dry_run,
                )
                if status_error:
                    result.status_errors.append(
                        (record.source_id, jira_key, status_error)
                    )
                if status_changed:
                    result.status_updates.append((record.source_id, jira_key))
                if is_existing:
                    comment_errors, comments_added = _add_missing_comments(
                        client,
                        record,
                        jira_key,
                    )
                    result.comment_errors.extend(
                        (record.source_id, jira_key, error)
                        for error in comment_errors
                    )
                    result.comments_added += comments_added
                    if fields_changed or status_changed or comments_added:
                        result.updated.append((record.source_id, jira_key))
                    else:
                        result.skipped.append(record.source_id)
                else:
                    comment_errors, comments_added = _add_comments(
                        client,
                        record,
                        jira_key,
                        dry_run,
                    )
                    result.comment_errors.extend(
                        (record.source_id, jira_key, error)
                        for error in comment_errors
                    )
                    result.comments_added += comments_added
                    result.created.append((record.source_id, jira_key))
            elif is_existing:
                if fields_changed:
                    result.updated.append((record.source_id, jira_key))
                else:
                    result.skipped.append(record.source_id)
        elif not dry_run:
            result.failed.append((record.source_id, creation_error or "Issue creation failed"))
        if progress_callback:
            progress_callback(
                "records",
                index,
                record_total,
                f"Imported {record.source_id}" if jira_key else f"Failed {record.source_id}",
            )

    # Phase 2: Create links between issues
    if not dry_run:
        link_types = client.get_link_types()
        # Prefer "Relates" or any available link type
        link_type_name = "Relates"
        if link_types:
            names = [lt.get("name", "") for lt in link_types]
            if "Relates" not in names:
                link_type_name = names[0] if names else "Relates"

        link_total = sum(len(record.links) for record in dataset.records)
        link_index = 0
        if progress_callback:
            progress_callback("links", 0, link_total, "Linking imported records")
        for record in dataset.records:
            from_key = source_to_jira.get(record.source_id)
            for linked_source_id in record.links:
                link_index += 1
                if not from_key:
                    if progress_callback:
                        progress_callback(
                            "links",
                            link_index,
                            link_total,
                            f"Skipping links from {record.source_id}",
                        )
                    continue
                to_key = source_to_jira.get(linked_source_id)
                if not to_key:
                    result.link_errors.append(
                        f"{record.source_id} → {linked_source_id}: target not imported"
                    )
                    continue
                if store.has_link(record.source_id, linked_source_id, db_path):
                    continue
                try:
                    client.create_issue_link(link_type_name, from_key, to_key)
                    store.record_link(record.source_id, linked_source_id, db_path)
                    result.links_created.append((record.source_id, linked_source_id))
                    logger.debug("LINKED %s → %s", from_key, to_key)
                except JiraClientError as e:
                    result.link_errors.append(f"{record.source_id} → {linked_source_id}: {e}")
                if progress_callback:
                    progress_callback(
                        "links",
                        link_index,
                        link_total,
                        f"Linked {record.source_id} → {linked_source_id}",
                    )
    elif dry_run:
        for record in dataset.records:
            for linked in record.links:
                logger.info("[DRY-RUN] Would link %s → %s", record.source_id, linked)

    return result
