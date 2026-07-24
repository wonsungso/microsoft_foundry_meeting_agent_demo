import json
from typing import Any
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from meeting_agent.remote import FoundryAgentClient
from meeting_agent.triage import TriageWorkflow


class FakeToken:
    token = "token"


class FakeCredential:
    def get_token(self, scope: str) -> FakeToken:
        assert scope == "https://ai.azure.com/.default"
        return FakeToken()


class FakeHttpResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"output_text": '{"sent":true}'}


class FakeHttpClient:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def post(
        self,
        endpoint: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeHttpResponse:
        assert endpoint.endswith(
            "/agents/notify-agent/endpoint/protocols/openai/responses?api-version=v1"
        )
        assert json["input"]
        self.headers = headers
        return FakeHttpResponse()


async def test_foundry_agent_client_injects_trace_context() -> None:
    client = FoundryAgentClient("https://example.test/api/projects/demo")
    client._credential = FakeCredential()  # type: ignore[assignment]
    fake_http = FakeHttpClient()
    client._http = fake_http  # type: ignore[assignment]

    def capture(carrier: dict[str, str]) -> None:
        carrier["traceparent"] = "00-test-trace-test-span-01"

    with patch("meeting_agent.remote.inject", side_effect=capture):
        result = await client.invoke("notify-agent", {"value": 1})

    assert result == {"sent": True}
    assert fake_http.headers["traceparent"] == "00-test-trace-test-span-01"
    assert fake_http.headers["Authorization"] == "Bearer token"


class FakeFoundryAgentClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((agent_name, payload))
        if agent_name == "context-agent":
            return {
                "room": "회의실",
                "meeting": {
                    "title": "AI Glasses 엣지 런타임 운영 리뷰",
                    "start_in_min": 0,
                    "attendees": 5,
                    "agenda_brief": "운영 설계를 확정합니다.",
                },
            }
        if agent_name == "meeting-agent":
            return {
                "minutes": {
                    "title": "AI Glasses 엣지 런타임 운영 리뷰",
                    "meeting_objective": "엣지 런타임 운영 방식을 확정합니다.",
                    "overview": "운영 설계와 배포 전략을 상세히 검토했습니다.",
                    "discussion_topics": [
                        {
                            "topic": "배포 ring 분리",
                            "details": "장애 격리와 단계적 검증을 위해 ring 분리를 논의했습니다.",
                            "conclusions": ["배포 ring을 분리합니다."],
                            "open_questions": [],
                        }
                    ],
                    "decisions": ["배포 ring을 분리합니다."],
                    "action_items": [],
                    "risks": ["초기 운영 복잡도가 증가합니다."],
                    "open_questions": [],
                    "follow_up_plan": ["다음 회의에서 부하 테스트 결과를 검토합니다."],
                },
                "artifact_name": "minutes.md",
                "artifact_url": "https://example.test/minutes.md",
            }
        if agent_name == "summarizer-agent":
            return {
                "title": "AI Glasses 엣지 런타임 운영 리뷰",
                "summary": "간결한 회의 요약",
                "decisions": ["배포 ring을 분리합니다."],
                "action_items": [],
            }
        if agent_name == "notify-agent":
            return {"sent": True}
        raise AssertionError(f"Unexpected agent: {agent_name}")


def _graph(client: FakeFoundryAgentClient) -> Any:
    return TriageWorkflow(
        client=client,  # type: ignore[arg-type]
        context_agent="context-agent",
        meeting_notes_agent="meeting-agent",
        summarizer_agent="summarizer-agent",
        notify_agent="notify-agent",
        recipient="admin@example.com",
    ).compile()


def _last_json(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["messages"][-1].content)


async def test_triage_delegates_room_analysis_to_context_agent() -> None:
    client = FakeFoundryAgentClient()
    result = await _graph(client).ainvoke(
        {
            "messages": [
                HumanMessage(content='{"tool":"analyze_room","ocr_texts":["회의실"]}')
            ]
        }
    )

    assert _last_json(result)["room"] == "회의실"
    assert [name for name, _ in client.calls] == ["context-agent"]


async def test_triage_runs_remote_meeting_handoff_in_order() -> None:
    client = FakeFoundryAgentClient()
    request = {
        "tool": "summarize_meeting",
        "transcript": "결정: 배포 ring을 분리합니다.",
        "meeting": {"title": "AI Glasses 엣지 런타임 운영 리뷰", "attendees": 5},
        "frames": [],
    }
    result = await _graph(client).ainvoke(
        {"messages": [HumanMessage(content=json.dumps(request, ensure_ascii=False))]}
    )

    assert _last_json(result) == {"done": True}
    assert [name for name, _ in client.calls] == [
        "meeting-agent",
        "summarizer-agent",
        "notify-agent",
    ]
    notify_payload = client.calls[-1][1]
    summarizer_payload = client.calls[-2][1]
    assert summarizer_payload["minutes"]["discussion_topics"][0]["topic"] == "배포 ring 분리"
    assert notify_payload["artifact_url"] == "https://example.test/minutes.md"
    assert notify_payload["minutes"]["summary"] == "간결한 회의 요약"
    assert "discussion_topics" not in notify_payload["minutes"]
