from __future__ import annotations

import json
from types import SimpleNamespace

from agent import agent
from agent.gemini import GeminiFunctionCall, GeminiTurn


def test_tool_call_arguments_remain_objects_in_followup_messages(monkeypatch):
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="test_tool",
            arguments={"release": "R2.4"},
        ),
    )
    responses = [
        SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call]),
            prompt_eval_count=1,
            eval_count=1,
        ),
        SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps({"overall_verdict": "READY", "requirements": []}),
                tool_calls=[],
            ),
            prompt_eval_count=1,
            eval_count=1,
        ),
    ]
    captured_messages = []

    class FakeOllamaClient:
        def __init__(self, host):
            pass

        def chat(self, **kwargs):
            captured_messages.append(kwargs["messages"])
            return responses.pop(0)

    monkeypatch.setattr(agent.ollama, "Client", FakeOllamaClient)
    monkeypatch.setattr(agent, "dispatch_tool", lambda *args: {"ok": True})

    trace = agent.run_assessment(
        client=object(),
        project_key="TYN",
        label="test",
        release="R2.4",
        commit="abc123",
        service="service",
        environment="production",
        prompt_version="p1",
    )

    assistant_message = next(
        message for message in captured_messages[1] if "tool_calls" in message
    )
    assistant_tool_call = assistant_message["tool_calls"][0]
    assert assistant_tool_call["function"]["arguments"] == {"release": "R2.4"}
    assert trace.error == "Requirements were not successfully discovered from the scoped Jira search."
    assert trace.requirements == []
    assert len(trace.tool_calls) == 1


def test_gemini_provider_runs_tools_and_collects_usage(monkeypatch):
    turns = [
        GeminiTurn(
            text="",
            function_calls=[
                GeminiFunctionCall(
                    name="search_requirements",
                    arguments={"release": "R2.4"},
                )
            ],
            total_tokens=13,
        ),
        GeminiTurn(
            text='{"requirements": []}',
            function_calls=[],
            total_tokens=7,
        ),
    ]
    submitted_results = []

    class FakeGeminiConversation:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"

        def generate(self):
            return turns.pop(0)

        def submit_tool_result(self, name, result):
            submitted_results.append((name, result))

        def close(self):
            pass

    monkeypatch.setattr(agent, "GeminiConversation", FakeGeminiConversation)
    monkeypatch.setattr(
        agent,
        "dispatch_tool",
        lambda name, *args: {"tool": name, "issues": []},
    )

    trace = agent.run_assessment(
        client=object(),
        project_key="TYN",
        label="test",
        release="R2.4",
        commit="abc123",
        service="service",
        environment="production",
        prompt_version="p1",
        provider="gemini",
        gemini_api_key="test-key",
    )

    assert trace.provider == "gemini"
    assert trace.total_tokens == 20
    assert len(trace.tool_calls) == 1
    assert submitted_results == [
        ("search_requirements", {"tool": "search_requirements", "issues": []})
    ]
