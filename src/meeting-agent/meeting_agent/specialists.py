from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict

from .contracts import (
    AnalyzeRoomRequest,
    AnalyzeRoomResponse,
    DetailedMeetingMinutes,
    ExecutiveMeetingSummary,
    SummarizeMeetingRequest,
)
from .mail import SummaryMailer
from .storage import MinutesArtifact, MinutesRepository
from .workflow import _candidate_room, _message_text

logger = logging.getLogger(__name__)


class MeetingNotesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: DetailedMeetingMinutes
    artifact_name: str
    artifact_url: str


class SpecialistState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    payload: dict[str, Any]
    response_json: str


def _finish(state: SpecialistState) -> SpecialistState:
    return {"messages": [AIMessageChunk(content=state["response_json"])]}


def _base_graph(parse: Any, execute: Any) -> Any:
    graph = StateGraph(SpecialistState)
    graph.add_node("parse", parse)
    graph.add_node("execute", execute)
    graph.add_node("finish", _finish)
    graph.add_edge(START, "parse")
    graph.add_edge("parse", "execute")
    graph.add_edge("execute", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


def build_context_graph(model: BaseChatModel) -> Any:
    agent = create_agent(
        model,
        system_prompt=(
            "You are the Context Agent. Use only the supplied demo calendar and return "
            "the recognized meeting room plus a concise Korean agenda briefing."
        ),
        response_format=AnalyzeRoomResponse,
    )

    async def parse(state: SpecialistState) -> SpecialistState:
        request = AnalyzeRoomRequest.model_validate_json(_message_text(state["messages"][-1]))
        return {"payload": request.model_dump(mode="json")}

    async def execute(state: SpecialistState) -> SpecialistState:
        request = AnalyzeRoomRequest.model_validate(state["payload"])
        room = _candidate_room(request.ocr_texts)
        if room is None:
            response = AnalyzeRoomResponse(room=None, meeting=None)
        else:
            result = await agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=json.dumps(
                                {
                                    "recognized_room": room,
                                    "ocr_texts": request.ocr_texts,
                                    "demo_calendar": {
                                        "title": "AI Glasses 엣지 런타임 운영 리뷰",
                                        "start_in_min": 0,
                                        "attendees": 5,
                                        "agenda": "AI Glasses 엣지 처리와 IoT Hub 운영 설계 확정",
                                    },
                                },
                                ensure_ascii=False,
                            )
                        )
                    ]
                }
            )
            response = AnalyzeRoomResponse.model_validate(result.get("structured_response"))
        return {"response_json": response.model_dump_json()}

    return _base_graph(parse, execute)


def build_meeting_notes_graph(model: BaseChatModel, repository: MinutesRepository) -> Any:
    agent = create_agent(
        model,
        system_prompt=(
            "You are the Meeting Notes Agent. Produce comprehensive, archival Korean meeting "
            "minutes, not an email summary. Expand every source-supported topic with its context, "
            "discussion flow, technical details, alternatives, conclusions, unresolved questions, "
            "risks, and follow-up plan. Preserve every explicit decision, owner, deadline, "
            "measurement, and operational fact. Aim for substantial detail across multiple "
            "discussion topics, but never invent facts that are absent from the transcript."
        ),
        response_format=DetailedMeetingMinutes,
    )

    async def parse(state: SpecialistState) -> SpecialistState:
        request = SummarizeMeetingRequest.model_validate_json(_message_text(state["messages"][-1]))
        return {"payload": request.model_dump(mode="json")}

    async def execute(state: SpecialistState) -> SpecialistState:
        request = SummarizeMeetingRequest.model_validate(state["payload"])
        result = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
                    )
                ]
            }
        )
        minutes = DetailedMeetingMinutes.model_validate(result.get("structured_response"))
        artifact = await repository.save(minutes, request.frames, request.transcript)
        response = MeetingNotesResult(
            minutes=minutes,
            artifact_name=artifact.name,
            artifact_url=artifact.url,
        )
        return {"response_json": response.model_dump_json()}

    return _base_graph(parse, execute)


def build_summarizer_graph(model: BaseChatModel) -> Any:
    agent = create_agent(
        model,
        system_prompt=(
            "You are the Summarizer Agent. Convert the supplied detailed minutes into a short, "
            "email-ready Korean executive summary. Keep the summary to 2-4 sentences and under "
            "700 Korean characters. Return at most five concise decisions and five high-priority "
            "action items. Do not copy detailed discussion, risks, open questions, or transcript "
            "passages. Preserve names, deadlines, measurements, and decisions exactly."
        ),
        response_format=ExecutiveMeetingSummary,
    )

    async def parse(state: SpecialistState) -> SpecialistState:
        payload = json.loads(_message_text(state["messages"][-1]))
        if not isinstance(payload, dict):
            raise TypeError("Summarizer input must be an object.")
        return {"payload": payload}

    async def execute(state: SpecialistState) -> SpecialistState:
        minutes = DetailedMeetingMinutes.model_validate(state["payload"]["minutes"])
        result = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(content=minutes.model_dump_json())
                ]
            }
        )
        summarized = ExecutiveMeetingSummary.model_validate(
            result.get("structured_response")
        )
        return {"response_json": summarized.model_dump_json()}

    return _base_graph(parse, execute)


def build_notify_graph(mailer: SummaryMailer, default_recipient: str) -> Any:
    async def parse(state: SpecialistState) -> SpecialistState:
        payload = json.loads(_message_text(state["messages"][-1]))
        if not isinstance(payload, dict):
            raise TypeError("Notify input must be an object.")
        return {"payload": payload}

    async def execute(state: SpecialistState) -> SpecialistState:
        payload = state["payload"]
        minutes = ExecutiveMeetingSummary.model_validate(payload["minutes"])
        artifact = MinutesArtifact(
            name=str(payload["artifact_name"]),
            url=str(payload["artifact_url"]),
        )
        sent = await mailer.send(
            str(payload.get("recipient") or default_recipient),
            minutes,
            artifact,
        )
        return {"response_json": json.dumps({"sent": sent})}

    return _base_graph(parse, execute)