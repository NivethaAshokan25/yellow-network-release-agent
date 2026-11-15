"""Minimal Gemini GenerateContent client for the agent's manual tool loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import quote

import httpx


@dataclass
class GeminiFunctionCall:
    name: str
    arguments: Dict[str, Any]


@dataclass
class GeminiTurn:
    text: str
    function_calls: List[GeminiFunctionCall]
    total_tokens: int


def _function_declarations(tool_definitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    declarations = []
    for tool in tool_definitions:
        function = tool["function"]
        declarations.append({
            "name": function["name"],
            "description": function["description"],
            "parameters": function["parameters"],
        })
    return declarations


class GeminiConversation:
    def __init__(
        self,
        api_key: str,
        model: str,
        system_instruction: str,
        user_prompt: str,
        tool_definitions: List[Dict[str, Any]],
    ):
        self.model = model
        self.system_instruction = system_instruction
        self.contents: List[Dict[str, Any]] = [{
            "role": "user",
            "parts": [{"text": user_prompt}],
        }]
        self.function_declarations = _function_declarations(tool_definitions)
        self._http = httpx.Client(timeout=120.0)
        self._headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        self._http.close()

    def generate(self) -> GeminiTurn:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent"
        )
        response = self._http.post(
            url,
            headers=self._headers,
            json={
                "systemInstruction": {
                    "parts": [{"text": self.system_instruction}],
                },
                "contents": self.contents,
                "tools": [{
                    "functionDeclarations": self.function_declarations,
                }],
                "toolConfig": {
                    "functionCallingConfig": {"mode": "AUTO"},
                },
            },
        )
        if not response.is_success:
            try:
                error = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                error = response.text
            raise RuntimeError(f"Gemini API error {response.status_code}: {error}")

        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates or not isinstance(candidates[0].get("content"), dict):
            feedback = payload.get("promptFeedback", {}).get("blockReason")
            detail = f" (block reason: {feedback})" if feedback else ""
            raise RuntimeError(f"Gemini returned no candidate content{detail}")

        candidate_content = candidates[0]["content"]
        self.contents.append(candidate_content)
        parts = candidate_content.get("parts", [])
        function_calls = [
            GeminiFunctionCall(
                name=part["functionCall"]["name"],
                arguments=part["functionCall"].get("args") or {},
            )
            for part in parts
            if isinstance(part.get("functionCall"), dict)
        ]
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part.get("text"), str)
        )
        usage = payload.get("usageMetadata", {})
        return GeminiTurn(
            text=text,
            function_calls=function_calls,
            total_tokens=usage.get("totalTokenCount", 0),
        )

    def submit_tool_result(self, name: str, result: Dict[str, Any]) -> None:
        self.contents.append({
            "role": "user",
            "parts": [{
                "functionResponse": {
                    "name": name,
                    "response": result,
                },
            }],
        })
