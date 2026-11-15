"""
Loads and validates inputs/dataset.json into Dataset model.
This module is ONLY used by the importer — never by the assessment agent.
"""
from __future__ import annotations

import json
from pathlib import Path

from importer.models import Dataset


def load_dataset(path: str | Path) -> Dataset:
    """Load and validate dataset.json. Raises on schema errors."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return Dataset.model_validate(raw)
