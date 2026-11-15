"""JSON-schema helpers for Jira tools exposed to model providers."""
from __future__ import annotations

from typing import Any, Optional


def tool_definition(
    name: str,
    description: str,
    properties: Optional[dict[str, dict[str, str]]] = None,
    required: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


def evidence_tool_definition(
    name: str,
    description: str,
    extra_properties: Optional[dict[str, dict[str, str]]] = None,
    extra_required: Optional[list[str]] = None,
) -> dict[str, Any]:
    properties = {
        "requirement_id": {"type": "string", "description": "Requirement ID being checked"},
        "issue_key": {"type": "string", "description": "Scoped Jira evidence issue key"},
        "comment_id": {
            "type": "string",
            "description": "Optional Jira comment ID; checks only that recorded comment",
        },
    }
    properties.update(extra_properties or {})
    return tool_definition(
        name,
        description,
        properties,
        ["requirement_id", "issue_key", *(extra_required or [])],
    )
