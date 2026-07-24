import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from meeting_agent.contracts import DetailedMeetingMinutes, DiscussionTopic, MeetingMinutes
from meeting_agent.mail import LocalOutboxMailer, ToolboxMailer
from meeting_agent.storage import LocalMinutesRepository, MinutesArtifact
from meeting_agent.workflow import MeetingWorkflow, _candidate_room


class FakeStructuredAgent:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls = 0

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"structured_response": self.response}


class FakeResponseModel:
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content=messages[-1].content)


def test_candidate_room_supports_generic_meeting_room() -> None:
    assert _candidate_room(["회의실", "AI Glasses 운영 리뷰"]) == "회의실"


async def test_toolbox_mailer_loads_tools_on_first_send() -> None:
    load_count = 0

    async def send_email(message: dict[str, Any]) -> str:
        return json.dumps(message)

    tool = StructuredTool.from_function(
        coroutine=send_email,
        name="WorkIQMail_send_email",
        description="Send an Outlook email.",
    )

    async def load_tools() -> list[StructuredTool]:
        nonlocal load_count
        load_count += 1
        return [tool]

    mailer = ToolboxMailer(tools_loader=load_tools)
    assert load_count == 0

    minutes = MeetingMinutes(
        title="MS미팅",
        summary="예산안을 승인했습니다.",
        decisions=["예산안 승인"],
        action_items=[],
    )
    artifact = MinutesArtifact(name="minutes.md", url="https://example.test/minutes.md")

    assert await mailer.send("admin@example.com", minutes, artifact)
    assert load_count == 1


async def test_toolbox_mailer_selects_send_mail_action() -> None:
    called_tools: list[str] = []

    async def send_email_with_attachments(input: str) -> str:
        called_tools.append("attachments")
        return input

    async def send_mail(message: dict[str, Any]) -> str:
        called_tools.append("sendMail")
        return json.dumps(message)

    tools = [
        StructuredTool.from_function(
            coroutine=send_email_with_attachments,
            name="WorkIQMail___SendEmailWithAttachments",
            description="Send an email with attachments.",
        ),
        StructuredTool.from_function(
            coroutine=send_mail,
            name="WorkIQMail___mcp_MailTools_graph_mail_sendMail",
            description="Send an Outlook email.",
        ),
    ]
    mailer = ToolboxMailer(tools)
    minutes = MeetingMinutes(
        title="MS미팅",
        summary="예산안을 승인했습니다.",
        decisions=["예산안 승인"],
        action_items=[],
    )
    artifact = MinutesArtifact(name="minutes.md", url="https://example.test/minutes.md")

    assert await mailer.send("admin@example.com", minutes, artifact)
    assert called_tools == ["sendMail"]


async def test_toolbox_mailer_creates_and_sends_draft() -> None:
    calls: list[tuple[str, str]] = []

    async def create_draft(to: list[str], subject: str, body: str) -> str:
        calls.append(("create", to[0]))
        return json.dumps({"message": "Draft created", "data": {"messageId": "draft-1"}})

    async def send_draft(id: str) -> str:
        calls.append(("send", id))
        return json.dumps({"message": "Draft sent successfully"})

    tools = [
        StructuredTool.from_function(
            coroutine=create_draft,
            name="WorkIQMail___CreateDraftMessage",
            description="Create an Outlook draft.",
        ),
        StructuredTool.from_function(
            coroutine=send_draft,
            name="WorkIQMail___SendDraftMessage",
            description="Send an Outlook draft.",
        ),
    ]
    mailer = ToolboxMailer(tools)
    minutes = MeetingMinutes(
        title="MS미팅",
        summary="예산안을 승인했습니다.",
        decisions=["예산안 승인"],
        action_items=[],
    )
    artifact = MinutesArtifact(name="minutes.md", url="https://example.test/minutes.md")

    assert await mailer.send("admin@example.com", minutes, artifact)
    assert calls == [("create", "admin@example.com"), ("send", "draft-1")]


async def test_toolbox_mailer_returns_false_on_tool_error() -> None:
    async def create_draft(to: list[str], subject: str, body: str) -> str:
        return json.dumps({"data": {"messageId": "draft-1"}})

    async def send_draft(id: str) -> str:
        return "Error: Error executing tool: A message needs at least one recipient."

    tools = [
        StructuredTool.from_function(
            coroutine=create_draft,
            name="WorkIQMail___CreateDraftMessage",
            description="Create an Outlook draft.",
        ),
        StructuredTool.from_function(
            coroutine=send_draft,
            name="WorkIQMail___SendDraftMessage",
            description="Send an Outlook draft.",
        ),
    ]
    mailer = ToolboxMailer(tools)
    minutes = MeetingMinutes(
        title="MS미팅",
        summary="예산안을 승인했습니다.",
        decisions=[],
        action_items=[],
    )
    artifact = MinutesArtifact(name="minutes.md", url="https://example.test/minutes.md")

    assert not await mailer.send("admin@example.com", minutes, artifact)


def _last_json(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["messages"][-1].content)


async def test_analyze_room_routes_to_room_subagent(tmp_path: Path) -> None:
    room_agent = FakeStructuredAgent(
        {
            "room": "11A04",
            "meeting": {
                "title": "MS미팅",
                "start_in_min": 12,
                "attendees": 2,
                "agenda_brief": "Foundry 데모를 검토할 예정입니다.",
            },
        }
    )
    minutes_agent = FakeStructuredAgent({})
    graph = MeetingWorkflow(
        room_agent,
        minutes_agent,
        FakeResponseModel(),
        LocalMinutesRepository(tmp_path / "minutes"),
        LocalOutboxMailer(tmp_path / "mail"),
        "admin@example.com",
    ).compile()

    message = HumanMessage(content='{"tool":"analyze_room","ocr_texts":["회의실 11A-04"]}')
    result = await graph.ainvoke({"messages": [message]})

    assert _last_json(result)["room"] == "11A04"
    assert room_agent.calls == 1
    assert minutes_agent.calls == 0


async def test_summarize_creates_markdown_and_mail(tmp_path: Path) -> None:
    minutes = MeetingMinutes(
        title="MS미팅",
        summary="예산안을 승인했습니다.",
        decisions=["예산안 승인"],
        action_items=[],
    )
    graph = MeetingWorkflow(
        FakeStructuredAgent({}),
        FakeStructuredAgent(minutes.model_dump()),
        FakeResponseModel(),
        LocalMinutesRepository(tmp_path / "minutes"),
        LocalOutboxMailer(tmp_path / "mail"),
        "admin@example.com",
    ).compile()

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=json.dumps(
                        {
                            "tool": "summarize_meeting",
                            "transcript": "결정: 예산안 승인",
                            "meeting": {"title": "MS미팅", "attendees": 2},
                            "frames": [],
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        }
    )

    assert _last_json(result) == {"done": True}
    minutes_files = list((tmp_path / "minutes").glob("*.md"))
    assert len(minutes_files) == 1
    markdown = minutes_files[0].read_text(encoding="utf-8")
    assert "# MS미팅" in markdown
    assert "## 회의 요약" in markdown
    assert "예산안을 승인했습니다." in markdown
    mail_files = list((tmp_path / "mail").glob("*.mail.json"))
    assert len(mail_files) == 1
    message = json.loads(mail_files[0].read_text(encoding="utf-8"))
    assert message["subject"] == "[AI Glasses] MS미팅 회의 요약"
    assert "핵심 요약" in message["body"]
    assert "전체 상세 회의록" in message["body"]


async def test_detailed_minutes_include_structured_sections_and_full_transcript(
    tmp_path: Path,
) -> None:
    transcript = "\n".join(
        f"발언 {index}: 엣지 런타임 운영, 장애 격리, telemetry 전달 기준을 검토했습니다."
        for index in range(1, 31)
    )
    minutes = DetailedMeetingMinutes(
        title="AI Glasses 운영 상세 회의",
        meeting_objective="엣지 런타임과 Cloud 전달 경계를 확정합니다.",
        overview="위치 데이터 처리, 장애 격리, 운영 관측성의 세 축을 중심으로 검토했습니다.",
        discussion_topics=[
            DiscussionTopic(
                topic="엣지 데이터 처리",
                details=(
                    "원본 벡터와 좌표계, quaternion, confidence를 엣지에서 보존하는 방식과 "
                    "Cloud로 전달할 최소 telemetry 범위를 비교했습니다. 운영 중 재현성과 "
                    "네트워크 비용 사이의 trade-off를 검토했습니다."
                ),
                conclusions=["원본 위치 데이터는 엣지 메시지에 보존합니다."],
                open_questions=["장기 보존 기간은 후속 회의에서 정합니다."],
            )
        ],
        decisions=["Cloud에는 위치 telemetry와 공간 식별 결과만 전달합니다."],
        action_items=[],
        risks=["Hub failover 시 순간 부하가 증가할 수 있습니다."],
        open_questions=["장기 보존 기간을 확정해야 합니다."],
        follow_up_plan=["부하 테스트 보고서를 다음 회의에서 검토합니다."],
    )
    repository = LocalMinutesRepository(tmp_path / "minutes")

    artifact = await repository.save(minutes, [], transcript)
    markdown = (tmp_path / "minutes" / artifact.name).read_text(encoding="utf-8")

    assert "## 상세 논의" in markdown
    assert "## 리스크 및 주의 사항" in markdown
    assert "## 미해결 질문" in markdown
    assert "## 원문 기록" in markdown
    assert transcript in markdown
    assert len(markdown) > len(transcript) + 500


class FailingRepository:
    async def save(self, minutes: MeetingMinutes, frames: list[str]) -> MinutesArtifact:
        raise RuntimeError("storage unavailable")


async def test_summarize_returns_false_when_storage_fails(tmp_path: Path) -> None:
    graph = MeetingWorkflow(
        FakeStructuredAgent({}),
        FakeStructuredAgent(
            {"title": "회의", "summary": "요약", "decisions": [], "action_items": []}
        ),
        FakeResponseModel(),
        FailingRepository(),
        LocalOutboxMailer(tmp_path / "mail"),
        "admin@example.com",
    ).compile()

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content='{"tool":"summarize_meeting","transcript":"기록 없음","frames":[]}'
                )
            ]
        }
    )

    assert _last_json(result) == {"done": False}
