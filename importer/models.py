"""
Pydantic models for dataset.json records.
Only used in the importer — never in assessment agent.
"""
from __future__ import annotations

import re
from typing import List, Optional
from pydantic import BaseModel, field_validator


class RecordedEvent(BaseModel):
    """Parsed from comment text: 'Recorded event <TS> | actor <Name> | role <Role>\n<body>'"""
    timestamp: str
    actor: str
    role: str
    body: str


def parse_recorded_event(comment_text: str) -> Optional[RecordedEvent]:
    """Extract structured metadata from a comment string."""
    pattern = r"Recorded event (\S+) \| actor ([^|]+) \| role ([^\n]+)\n(.*)"
    m = re.match(pattern, comment_text, re.DOTALL)
    if not m:
        return None
    return RecordedEvent(
        timestamp=m.group(1).strip(),
        actor=m.group(2).strip(),
        role=m.group(3).strip(),
        body=m.group(4).strip(),
    )


class DatasetRecord(BaseModel):
    source_id: str
    kind: str  # 'evidence' | 'work' | 'requirement' | 'manifest'
    summary: str
    release: str
    recorded_status: str
    description: str
    comments: List[str]
    links: List[str]

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        allowed = {"evidence", "work", "requirement", "manifest"}
        if v not in allowed:
            raise ValueError(f"Unknown kind: {v!r}, expected one of {allowed}")
        return v

    def parsed_comments(self) -> List[RecordedEvent]:
        """Return successfully parsed RecordedEvent objects for each comment."""
        events = []
        for c in self.comments:
            ev = parse_recorded_event(c)
            if ev:
                events.append(ev)
        return events


class DatasetTarget(BaseModel):
    release: str
    commit: str
    prompt: str
    environment: str
    service: str


class Dataset(BaseModel):
    dataset_label: str
    target: DatasetTarget
    records: List[DatasetRecord]
    note: Optional[str] = None

    @property
    def manifest(self) -> Optional[DatasetRecord]:
        for r in self.records:
            if r.kind == "manifest":
                return r
        return None

    @property
    def requirements(self) -> List[DatasetRecord]:
        return [r for r in self.records if r.kind == "requirement"]

    def get_record(self, source_id: str) -> Optional[DatasetRecord]:
        for r in self.records:
            if r.source_id == source_id:
                return r
        return None
