"""Check whether a later Jira comment explicitly supersedes an earlier one."""
from datetime import datetime
import re
from typing import Any

from evaluation.dimensions import check_supersession as evaluate_supersession
from jira.client import JiraClient, JiraClientError
from .common import extract_text_from_adf, get_scoped_issue
from .tool_schema import tool_definition

TOOL_DEFINITION = tool_definition(
    "check_supersession",
    "Check if a later Jira comment explicitly supersedes an earlier comment.",
    {
        "requirement_id": {"type": "string"},
        "issue_key": {"type": "string"},
        "earlier_comment_id": {"type": "string"},
        "later_comment_id": {"type": "string"},
    },
    ["requirement_id", "issue_key", "earlier_comment_id", "later_comment_id"],
)


def check_supersession(
    client: JiraClient,
    project_key: str,
    label: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    requirement_id = arguments.get("requirement_id", "")
    issue_key = arguments.get("issue_key", "")
    earlier_comment_id = arguments.get("earlier_comment_id", "")
    later_comment_id = arguments.get("later_comment_id", "")
    try:
        get_scoped_issue(client, issue_key, project_key, label)
        comments = client.get_comments(issue_key)
        earlier_comment = next(
            (comment for comment in comments if str(comment.get("id")) == str(earlier_comment_id)),
            None,
        )
        later_comment = next(
            (comment for comment in comments if str(comment.get("id")) == str(later_comment_id)),
            None,
        )
        if earlier_comment is None or later_comment is None:
            raise ValueError("Both supersession comments must exist on the scoped Jira issue")

        earlier = extract_text_from_adf(earlier_comment.get("body"))
        later = extract_text_from_adf(later_comment.get("body"))
        earlier_ref = f"{issue_key}#comment-{earlier_comment_id}"
        later_ref = f"{issue_key}#comment-{later_comment_id}"
        earlier_time = _recorded_event_time(earlier)
        later_time = _recorded_event_time(later)
        if later_time <= earlier_time:
            raise ValueError("The superseding comment's recorded event time is not later")

        dimension = evaluate_supersession(earlier, later)
        return {
            "tool": "check_supersession",
            "requirement_id": requirement_id,
            "source_ref": later_ref,
            "earlier_ref": earlier_ref,
            "later_ref": later_ref,
            "applicable": dimension.applicable,
            "reason": dimension.reason,
            "chronology_valid": True,
        }
    except (JiraClientError, ValueError) as exc:
        return {
            "tool": "check_supersession",
            "requirement_id": requirement_id,
            "source_ref": issue_key,
            "error": str(exc),
        }


def _recorded_event_time(comment_text: str) -> datetime:
    match = re.search(r"^Recorded event (\S+) \| actor [^|]+\| role [^\r\n]+", comment_text)
    if not match:
        raise ValueError("Supersession comments must include recorded event timestamps")
    value = match.group(1).replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("A recorded event timestamp is not valid ISO-8601") from exc
    if timestamp.tzinfo is None:
        raise ValueError("Recorded event timestamps must include a timezone")
    return timestamp
