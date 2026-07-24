from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from .contracts import (
    AnalyzeRoomRequest,
    AnalyzeRoomResponse,
    MeetingMinutes,
    SummarizeMeetingRequest,
    parse_request,
)
from .mail import SummaryMailer
from .storage import MinutesArtifact, MinutesRepository

logger = logging.getLogger(__name__)


class MeetingState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    request: AnalyzeRoomRequest | SummarizeMeetingRequest
    minutes: MeetingMinutes
    artifact: MinutesArtifact
    response_json: str
    error: str


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for item in message.content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def _candidate_room(ocr_texts: list[str]) -> str | None:
    for text in ocr_texts:
        if "회의실" in text:
            return "회의실"
        named_room = re.search(r"\bROOM\s*[-:]?\s*([A-Z0-9]+)\b", text.upper())
        if named_room:
            return f"ROOM-{named_room.group(1)}"
        match = re.search(r"\b(\d{1,2}[A-Z]-?\d{1,2})\b", text.upper())
        if match:
            return match.group(1).replace("-", "")
    return None


class MeetingWorkflow:
    def __init__(
        self,
        room_agent: Any,
        minutes_agent: Any,
        response_model: Any,
        repository: MinutesRepository,
        mailer: SummaryMailer,
        recipient: str,
    ) -> None:
        self._room_agent = room_agent
        self._minutes_agent = minutes_agent
        self._response_model = response_model
        self._repository = repository
        self._mailer = mailer
        self._recipient = recipient

    async def parse(self, state: MeetingState) -> MeetingState:
        try:
            raw = _message_text(state["messages"][-1])
            return {"request": parse_request(raw)}
        except (IndexError, json.JSONDecodeError, ValidationError, ValueError) as error:
            return {
                "error": "invalid_request",
                "response_json": json.dumps(
                    {"done": False, "error": {"code": "invalid_request", "message": str(error)}},
                    ensure_ascii=False,
                ),
            }

    def route(self, state: MeetingState) -> Literal["analyze", "summarize", "finish"]:
        request = state.get("request")
        if isinstance(request, AnalyzeRoomRequest):
            return "analyze"
        if isinstance(request, SummarizeMeetingRequest):
            return "summarize"
        return "finish"

    async def analyze(self, state: MeetingState) -> MeetingState:
        request = state["request"]
        assert isinstance(request, AnalyzeRoomRequest)
        room = _candidate_room(request.ocr_texts)
        if room is None:
            response = AnalyzeRoomResponse(room=None, meeting=None)
        else:
            prompt = {
                "recognized_room": room,
                "ocr_texts": request.ocr_texts,
                "demo_calendar": {
                    "title": "AI Glasses 엣지 런타임 운영 리뷰",
                    "start_in_min": 0,
                    "attendees": 5,
                    "agenda": (
                        "AI Glasses 위치 추정 이벤트를 엣지에서 안정적으로 처리하고 "
                        "Azure IoT Hub까지 전달하는 운영 설계를 확정합니다."
                    ),
                },
            }
            try:
                result = await self._room_agent.ainvoke(
                    {"messages": [HumanMessage(content=json.dumps(prompt, ensure_ascii=False))]}
                )
                structured = result.get("structured_response")
                response = AnalyzeRoomResponse.model_validate(structured)
            except Exception:
                logger.exception("Room-analysis SubAgent failed")
                response = AnalyzeRoomResponse(room=room, meeting=None)
        return {"response_json": response.model_dump_json()}

    async def summarize(self, state: MeetingState) -> MeetingState:
        request = state["request"]
        assert isinstance(request, SummarizeMeetingRequest)
        title = request.meeting.title if request.meeting else "AI Glasses 회의"
        attendees = request.meeting.attendees if request.meeting else 0
        try:
            result = await self._minutes_agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=json.dumps(
                                {
                                    "title": title,
                                    "attendees": attendees,
                                    "transcript": request.transcript,
                                    "frames": request.frames,
                                },
                                ensure_ascii=False,
                            )
                        )
                    ]
                }
            )
            minutes = MeetingMinutes.model_validate(result.get("structured_response"))
            return {"minutes": minutes}
        except Exception as error:
            logger.exception("Meeting-minutes SubAgent failed")
            return {"error": type(error).__name__, "response_json": json.dumps({"done": False})}

    async def persist(self, state: MeetingState) -> MeetingState:
        request = state["request"]
        assert isinstance(request, SummarizeMeetingRequest)
        try:
            artifact = await self._repository.save(state["minutes"], request.frames)
            return {"artifact": artifact}
        except Exception as error:
            logger.exception("Meeting minutes persistence failed")
            return {"error": type(error).__name__, "response_json": json.dumps({"done": False})}

    async def notify(self, state: MeetingState) -> MeetingState:
        try:
            sent = await self._mailer.send(
                recipient=self._recipient,
                minutes=state["minutes"],
                artifact=state["artifact"],
            )
            return {"response_json": json.dumps({"done": sent})}
        except Exception as error:
            logger.exception("Meeting summary mail failed")
            return {"error": type(error).__name__, "response_json": json.dumps({"done": False})}

    def continue_if_successful(self, state: MeetingState) -> Literal["continue", "finish"]:
        return "finish" if state.get("error") else "continue"

    async def finish(self, state: MeetingState) -> MeetingState:
        response = await self._response_model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Return the user's JSON object exactly. Do not add, remove, translate, "
                        "explain, or wrap any field. Output JSON only."
                    )
                ),
                HumanMessage(content=state["response_json"]),
            ]
        )
        return {"messages": [response]}

    def compile(self) -> Any:
        graph = StateGraph(MeetingState)
        graph.add_node("parse", self.parse)
        graph.add_node("analyze", self.analyze)
        graph.add_node("summarize", self.summarize)
        graph.add_node("persist", self.persist)
        graph.add_node("notify", self.notify)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "parse")
        graph.add_conditional_edges(
            "parse",
            self.route,
            {"analyze": "analyze", "summarize": "summarize", "finish": "finish"},
        )
        graph.add_edge("analyze", "finish")
        graph.add_conditional_edges(
            "summarize",
            self.continue_if_successful,
            {"continue": "persist", "finish": "finish"},
        )
        graph.add_conditional_edges(
            "persist",
            self.continue_if_successful,
            {"continue": "notify", "finish": "finish"},
        )
        graph.add_edge("notify", "finish")
        graph.add_edge("finish", END)
        return graph.compile()


def build_meeting_graph(
    model: BaseChatModel,
    repository: MinutesRepository,
    mailer: SummaryMailer,
    recipient: str,
) -> Any:
    room_agent = create_agent(
        model,
        system_prompt=(
            "You are the room-analysis SubAgent. Use only the supplied demo calendar. "
            "Return the recognized room and a concise Korean agenda briefing."
        ),
        response_format=AnalyzeRoomResponse,
    )
    minutes_agent = create_agent(
        model,
        system_prompt=(
            "You are the meeting-minutes SubAgent. Produce concise Korean minutes from the "
            "transcript. Preserve explicit decisions and action owners; never invent facts."
        ),
        response_format=MeetingMinutes,
    )
    response_model = model.bind(response_format={"type": "json_object"})
    return MeetingWorkflow(
        room_agent,
        minutes_agent,
        response_model,
        repository,
        mailer,
        recipient,
    ).compile()
