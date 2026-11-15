from __future__ import annotations

import pytest

from jira.client import JiraClient, JiraClientError


def test_search_issues_uses_current_jql_endpoint():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    client._request = lambda method, path, **kwargs: {
        "method": method,
        "path": path,
        **kwargs,
    }

    result = client.search_issues("project = TYN", fields=["summary"], max_results=50)

    assert result["path"] == "/rest/api/3/search/jql"
    assert result["params"] == {
        "jql": "project = TYN",
        "maxResults": 50,
        "fields": ["summary"],
    }


def test_search_issues_requests_fields_needed_by_agent():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    client._request = lambda method, path, **kwargs: kwargs

    result = client.search_issues("project = TYN")

    assert result["params"]["fields"] == [
        "summary",
        "description",
        "status",
        "labels",
        "issuelinks",
        "issuetype",
        "project",
    ]


def test_search_issues_fails_if_jira_repeats_a_results_page():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    responses = [
        {
            "issues": [{"key": "TYN-1"}],
            "isLast": False,
            "nextPageToken": "next",
        },
        {"issues": [{"key": "TYN-1"}], "isLast": True},
    ]
    client._request = lambda *_args, **_kwargs: responses.pop(0)

    with pytest.raises(JiraClientError, match="repeated a results page"):
        client.search_issues_paginated("project = TYN")


def test_search_issues_paginated_uses_next_page_token():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    requests = []
    responses = [
        {
            "issues": [{"key": "TYN-1"}],
            "isLast": False,
            "nextPageToken": "next",
        },
        {"issues": [{"key": "TYN-2"}], "isLast": True},
    ]

    def fake_request(method, path, **kwargs):
        requests.append((method, path, kwargs["params"]))
        return responses.pop(0)

    client._request = fake_request

    issues = client.search_issues_paginated("project = TYN")

    assert [issue["key"] for issue in issues] == ["TYN-1", "TYN-2"]
    assert [request[1] for request in requests] == [
        "/rest/api/3/search/jql",
        "/rest/api/3/search/jql",
    ]
    assert "nextPageToken" not in requests[0][2]
    assert requests[1][2]["nextPageToken"] == "next"


def test_get_comments_fetches_all_pages():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    requests = []
    responses = [
        {"comments": [{"id": "1"}, {"id": "2"}], "startAt": 0, "total": 3},
        {"comments": [{"id": "3"}], "startAt": 2, "total": 3},
    ]

    def fake_request(method, path, **kwargs):
        requests.append((method, path, kwargs["params"]))
        return responses.pop(0)

    client._request = fake_request

    comments = client.get_comments("TYN-1")

    assert [comment["id"] for comment in comments] == ["1", "2", "3"]
    assert [request[2]["startAt"] for request in requests] == [0, 2]
    assert all(request[2]["maxResults"] == 100 for request in requests)


def test_get_comments_fails_if_jira_repeats_a_page():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    responses = [
        {"comments": [{"id": "1"}], "total": 2},
        {"comments": [{"id": "1"}], "total": 2},
    ]
    client._request = lambda *_args, **_kwargs: responses.pop(0)

    with pytest.raises(JiraClientError, match="repeated a page"):
        client.get_comments("TYN-1")


def test_get_comments_fails_if_more_results_are_advertised_without_total():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    client._request = lambda *_args, **_kwargs: {
        "comments": [{"id": "1"}],
        "isLast": False,
    }

    with pytest.raises(JiraClientError, match="without total"):
        client.get_comments("TYN-1")


def test_search_issues_fails_when_more_pages_are_advertised_without_token():
    client = JiraClient("https://example.atlassian.net", "user", "token")
    client._request = lambda *_args, **_kwargs: {
        "issues": [{"key": "TYN-1"}],
        "isLast": False,
    }

    with pytest.raises(JiraClientError, match="without a next-page token"):
        client.search_issues_paginated("project = TYN")
