from __future__ import annotations

import json
import logging
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessageChunk, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .contracts import (
    AnalyzeRoomRequest,
    AnalyzeRoomResponse,
    ExecutiveMeetingSummary,
    SummarizeMeetingRequest,
    parse_request,
)
from .remote import FoundryAgentClient
from .specialists import MeetingNotesResult
from .workflow import _message_text

logger = logging.getLogger(__name__)


class TriageState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    request: AnalyzeRoomRequest | SummarizeMeetingRequest
    notes: MeetingNotesResult
    summary: ExecutiveMeetingSummary
    response_json: str
    error: str


class TriageWorkflow:
    def __init__(
        self,
        client: FoundryAgentClient,
        context_agent: str,
        meeting_notes_agent: str,
        summarizer_agent: str,
        notify_agent: str,
        recipient: str,
    ) -> None:
        self._client = client
        self._context_agent = context_agent
        self._meeting_notes_agent = meeting_notes_agent
        self._summarizer_agent = summarizer_agent
        self._notify_agent = notify_agent
        self._recipient = recipient

    async def parse(self, state: TriageState) -> TriageState:
        try:
            return {"request": parse_request(_message_text(state["messages"][-1]))}
        except Exception as error:
            logger.exception("Triage request validation failed")
            return {
                "error": type(error).__name__,
                "response_json": json.dumps({"done": False}),
            }

    def route(self, state: TriageState) -> Literal["context", "notes", "finish"]:
        request = state.get("request")
        if isinstance(request, AnalyzeRoomRequest):
            return "context"
        if isinstance(request, SummarizeMeetingRequest):
            return "notes"
        return "finish"

    async def context(self, state: TriageState) -> TriageState:
        request = state["request"]
        assert isinstance(request, AnalyzeRoomRequest)
        response = await self._client.invoke(
            self._context_agent,
            request.model_dump(mode="json"),
        )
        validated = AnalyzeRoomResponse.model_validate(response)
        return {"response_json": validated.model_dump_json()}

    async def notes(self, state: TriageState) -> TriageState:
        request = state["request"]
        assert isinstance(request, SummarizeMeetingRequest)
        response = await self._client.invoke(
            self._meeting_notes_agent,
            request.model_dump(mode="json"),
        )
        return {"notes": MeetingNotesResult.model_validate(response)}

    async def summarize(self, state: TriageState) -> TriageState:
        response = await self._client.invoke(
            self._summarizer_agent,
            {"minutes": state["notes"].minutes.model_dump(mode="json")},
        )
        return {"summary": ExecutiveMeetingSummary.model_validate(response)}

    async def notify(self, state: TriageState) -> TriageState:
        notes = state["notes"]
        response = await self._client.invoke(
            self._notify_agent,
            {
                "minutes": state["summary"].model_dump(mode="json"),
                "artifact_name": notes.artifact_name,
                "artifact_url": notes.artifact_url,
                "recipient": self._recipient,
            },
        )
        return {"response_json": json.dumps({"done": response.get("sent") is True})}

    async def finish(self, state: TriageState) -> TriageState:
        return {"messages": [AIMessageChunk(content=state["response_json"])]}

    def compile(self) -> object:
        graph = StateGraph(TriageState)
        graph.add_node("parse", self.parse)
        graph.add_node("context", self.context)
        graph.add_node("notes", self.notes)
        graph.add_node("summarize", self.summarize)
        graph.add_node("notify", self.notify)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "parse")
        graph.add_conditional_edges(
            "parse",
            self.route,
            {"context": "context", "notes": "notes", "finish": "finish"},
        )
        graph.add_edge("context", "finish")
        graph.add_edge("notes", "summarize")
        graph.add_edge("summarize", "notify")
        graph.add_edge("notify", "finish")
        graph.add_edge("finish", END)
        return graph.compile()