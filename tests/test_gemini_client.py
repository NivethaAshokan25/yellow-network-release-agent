from __future__ import annotations

from copy import deepcopy

from agent.gemini import GeminiConversation
from tools.jira_tools import TOOL_DEFINITIONS


class FakeResponse:
    is_success = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, **kwargs):
        self.requests = []
        self.responses = [
            FakeResponse({
                "candidates": [{
                    "content": {
                        "role": "model",
                        "parts": [{
                            "functionCall": {
                                "name": "search_requirements",
                                "args": {"release": "R2.4"},
                            },
                        }],
                    },
                }],
                "usageMetadata": {"totalTokenCount": 13},
            }),
            FakeResponse({
                "candidates": [{
                    "content": {
                        "role": "model",
                        "parts": [{"text": '{"requirements": []}'}],
                    },
                }],
                "usageMetadata": {"totalTokenCount": 7},
            }),
        ]

    def post(self, url, headers, json):
        self.requests.append({
            "url": url,
            "headers": headers,
            "json": deepcopy(json),
        })
        return self.responses.pop(0)

    def close(self):
        pass


def test_gemini_tool_loop_uses_jira_tool_and_keeps_key_in_header(monkeypatch):
    monkeypatch.setattr("agent.gemini.httpx.Client", FakeHttpClient)
    conversation = GeminiConversation(
        api_key="test-key",
        model="gemini-3.5-flash-lite",
        system_instruction="system",
        user_prompt="assess",
        tool_definitions=TOOL_DEFINITIONS,
    )

    tool_turn = conversation.generate()
    assert tool_turn.function_calls[0].name == "search_requirements"
    assert tool_turn.function_calls[0].arguments == {"release": "R2.4"}
    assert tool_turn.total_tokens == 13
    conversation.submit_tool_result(
        "search_requirements",
        {"issues": [{"key": "TYN-1"}]},
    )
    final_turn = conversation.generate()

    assert final_turn.text == '{"requirements": []}'
    assert not final_turn.function_calls
    assert final_turn.total_tokens == 7
    first_request = conversation._http.requests[0]
    assert first_request["headers"]["x-goog-api-key"] == "test-key"
    assert "test-key" not in first_request["url"]
    assert "functionDeclarations" in first_request["json"]["tools"][0]
    second_contents = conversation._http.requests[1]["json"]["contents"]
    assert second_contents[-1]["role"] == "user"
    assert (
        second_contents[-1]["parts"][0]["functionResponse"]["name"]
        == "search_requirements"
    )
