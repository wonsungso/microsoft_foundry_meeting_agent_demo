from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from azure.ai.agentserver.core import get_request_context
from azure.identity import DefaultAzureCredential
from opentelemetry.propagate import inject

_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


class FoundryAgentClient:
    def __init__(self, project_endpoint: str) -> None:
        self._project_endpoint = project_endpoint.rstrip("/")
        self._credential = DefaultAzureCredential()
        self._http = httpx.AsyncClient(timeout=180.0)

    async def invoke(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = await asyncio.to_thread(self._credential.get_token, _AZURE_AI_SCOPE)
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
            **get_request_context().platform_headers(),
        }
        inject(headers)
        endpoint = (
            f"{self._project_endpoint}/agents/{agent_name}/endpoint/"
            "protocols/openai/responses?api-version=v1"
        )
        response = await self._http.post(
            endpoint,
            headers=headers,
            json={
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": json.dumps(payload, ensure_ascii=False),
                            }
                        ],
                    }
                ]
            },
        )
        response.raise_for_status()
        body = response.json()
        text = self._output_text(body)
        if text is None:
            raise RuntimeError(f"Agent {agent_name} returned no output_text.")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise TypeError(f"Agent {agent_name} returned a non-object JSON response.")
        return parsed

    @staticmethod
    def _output_text(response: dict[str, Any]) -> str | None:
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        for item in response.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str):
                        return text
        return None