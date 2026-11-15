from __future__ import annotations

from evaluation.dimensions import Target
from tools.jira import TOOL_MODULES
from tools.jira.common import summarise_issue
from tools.jira_tools import TOOL_DEFINITIONS, dispatch_tool


TARGET = Target(
    release="R2.4",
    commit="b7a91f2",
    service="atlas-assistant",
    environment="production",
    prompt="p7",
)


class FakeJiraClient:
    def __init__(self, labels=None):
        self.issue = {
            "key": "TYN-42",
            "fields": {
                "project": {"key": "TYN"},
                "labels": labels or ["custom-scope"],
                "summary": "[REC-1] Test evidence",
                "description": "Imported description",
                "status": {
                    "name": "Open",
                    "statusCategory": {"key": "new"},
                },
                "issuelinks": [],
            },
        }
        self.comments = [
            {
                "id": "9001",
                "author": {"displayName": "Jira Import User"},
                "created": "2026-11-15T00:00:00Z",
                "body": (
                    "Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | "
                    "role QA Lead\nrelease R2.4; commit b7a91f2; service atlas-assistant; "
                    "environment production."
                ),
            }
        ]
        self.comments_requested = False

    def get_issue(self, issue_key):
        return self.issue

    def get_comments(self, issue_key):
        self.comments_requested = True
        return self.comments

    def search_issues(self, jql, max_results=100, fields=None):
        self.query = jql
        return {"issues": []}

    def search_issues_paginated(self, jql, fields=None):
        self.query = jql
        return []


def test_individual_check_tool_reads_scoped_jira_comment():
    client = FakeJiraClient()

    result = dispatch_tool(
        "check_commit",
        {
            "requirement_id": "REQ-01",
            "issue_key": "TYN-42",
            "comment_id": "9001",
        },
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert result["dimension"] == "commit"
    assert result["applicable"] is True
    assert result["source_ref"] == "TYN-42#comment-9001"
    assert client.comments_requested


def test_direct_issue_check_rejects_out_of_scope_ticket():
    client = FakeJiraClient(labels=["other-scope"])

    result = dispatch_tool(
        "check_release",
        {"requirement_id": "REQ-01", "issue_key": "TYN-42"},
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert result["error"]
    assert "outside" in result["error"]


def test_approval_check_requires_explicit_scenario_actor_role_and_signoff():
    client = FakeJiraClient()
    client.comments = [
        {
            "id": "9002",
            "body": (
                "Recorded event 2026-11-08T14:00:00+05:30 | actor Asha Rao | "
                "role Security Owner\nI explicitly approve release R2.4."
            ),
        }
    ]

    result = dispatch_tool(
        "check_approval_actor",
        {
            "requirement_id": "REQ-06",
            "issue_key": "TYN-42",
            "comment_id": "9002",
            "required_actor": "Asha Rao",
            "required_role": "Security Owner",
        },
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert result["applicable"] is True
    assert result["outcome"] == "PASS"
    assert result["source_ref"] == "TYN-42#comment-9002"


def test_open_defect_check_uses_jira_status_and_target_scope():
    client = FakeJiraClient()
    client.issue["fields"]["description"] = (
        "Failure record for release R2.4, commit b7a91f2, service atlas-assistant, "
        "environment production. Cross-customer exposure: FAIL."
    )

    result = dispatch_tool(
        "check_open_defect",
        {"requirement_id": "REQ-02", "issue_key": "TYN-42"},
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert result["applicable"] is True
    assert result["outcome"] == "FAIL"
    assert result["jira_status_category"] == "new"


def test_requirement_search_uses_selected_label():
    client = FakeJiraClient()

    dispatch_tool(
        "search_requirements",
        {"release": "R2.4"},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert 'labels = "selected-scope"' in client.query
    assert 'labels = "kind-requirement"' in client.query
    assert 'labels = "release-R2.4"' in client.query
    assert 'summary ~' not in client.query
    assert 'text ~' not in client.query


def test_manifest_search_uses_import_kind_label():
    client = FakeJiraClient()

    dispatch_tool(
        "get_manifest",
        {},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert 'labels = "selected-scope"' in client.query
    assert 'labels = "kind-manifest"' in client.query
    assert 'summary ~' not in client.query


def test_requirement_search_rejects_empty_release_without_jira_query():
    client = FakeJiraClient()

    result = dispatch_tool(
        "search_requirements",
        {"release": ""},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert result["error"] == "A target release is required."
    assert not hasattr(client, "query")


def test_search_defects_can_scope_to_environment():
    client = FakeJiraClient()

    dispatch_tool(
        "search_defects",
        {"release": "R2.4", "environment": "production"},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert 'text ~ "production"' in client.query


def test_search_defects_checks_evidence_text_not_only_issue_titles():
    client = FakeJiraClient()

    dispatch_tool(
        "search_defects",
        {"release": "R2.4"},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert 'text ~ "defect"' in client.query
    assert 'text ~ "incident"' in client.query
    assert "summary ~" not in client.query


def test_search_approvals_checks_evidence_text_not_only_issue_titles():
    client = FakeJiraClient()

    dispatch_tool(
        "search_approvals",
        {"release": "R2.4"},
        client,
        "TYN",
        "selected-scope",
        TARGET,
    )

    assert 'text ~ "approved"' in client.query
    assert 'text ~ "sign-off"' in client.query
    assert "summary ~" not in client.query


def test_requirement_summary_preserves_mandatory_flag():
    issue = {
        "key": "TYN-10",
        "fields": {
            "description": (
                "[source_id:REQ-10] kind=requirement\nMandatory: no\nAcceptance criterion: ..."
            )
        },
    }

    result = summarise_issue(issue)

    assert result["source_id"] == "REQ-10"
    assert result["mandatory"] is False


def test_tools_are_individual_evidence_checks_not_one_validator():
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

    assert {
        "check_release",
        "check_commit",
        "check_service",
        "check_environment",
        "check_prompt_version",
        "check_verification_outcome",
        "check_approval_actor",
        "check_open_defect",
        "check_supersession",
    }.issubset(names)
    assert "validate_entire_release" not in names


def test_each_callable_tool_has_its_own_module_and_schema():
    module_paths = {module.__file__ for module in TOOL_MODULES}
    module_names = {module.TOOL_DEFINITION["function"]["name"] for module in TOOL_MODULES}
    definition_names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

    assert len(module_paths) == len(TOOL_MODULES)
    assert module_names == definition_names


def test_supersession_requires_later_recorded_event_and_explicit_reference():
    client = FakeJiraClient()
    client.comments = [
        {
            "id": "9001",
            "body": (
                "Recorded event 2026-11-08T10:00:00+05:30 | actor Priya Nair | "
                "role QA Lead\nREDACT-870: FAIL on target build."
            ),
        },
        {
            "id": "9002",
            "body": (
                "Recorded event 2026-11-08T11:00:00+05:30 | actor Priya Nair | "
                "role QA Lead\nREDACT-901 explicitly supersedes REDACT-870."
            ),
        },
    ]

    result = dispatch_tool(
        "check_supersession",
        {
            "requirement_id": "REQ-07",
            "issue_key": "TYN-42",
            "earlier_comment_id": "9001",
            "later_comment_id": "9002",
        },
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert result["applicable"] is True
    assert result["chronology_valid"] is True
    assert result["earlier_ref"] == "TYN-42#comment-9001"


def test_supersession_rejects_out_of_order_recorded_events():
    client = FakeJiraClient()
    client.comments = [
        {
            "id": "9001",
            "body": (
                "Recorded event 2026-11-08T12:00:00+05:30 | actor Priya Nair | "
                "role QA Lead\nREDACT-870: FAIL on target build."
            ),
        },
        {
            "id": "9002",
            "body": (
                "Recorded event 2026-11-08T11:00:00+05:30 | actor Priya Nair | "
                "role QA Lead\nREDACT-901 explicitly supersedes REDACT-870."
            ),
        },
    ]

    result = dispatch_tool(
        "check_supersession",
        {
            "requirement_id": "REQ-07",
            "issue_key": "TYN-42",
            "earlier_comment_id": "9001",
            "later_comment_id": "9002",
        },
        client,
        "TYN",
        "custom-scope",
        TARGET,
    )

    assert "not later" in result["error"]
