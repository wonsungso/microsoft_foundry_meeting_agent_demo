from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRoomRequest(ContractModel):
    tool: Literal["analyze_room"]
    ocr_texts: list[str] = Field(max_length=100)

    @field_validator("ocr_texts")
    @classmethod
    def validate_ocr_texts(cls, values: list[str]) -> list[str]:
        if any(len(value) > 500 for value in values):
            raise ValueError("Each OCR text must be 500 characters or fewer.")
        return values


class MeetingContext(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    start_in_min: int
    attendees: int = Field(ge=0, le=10000)
    agenda_brief: str | None = Field(default=None, max_length=500)


class AnalyzeRoomResponse(ContractModel):
    room: str | None
    meeting: MeetingContext | None


class MeetingInput(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    attendees: int = Field(ge=0, le=10000)


class SummarizeMeetingRequest(ContractModel):
    tool: Literal["summarize_meeting"]
    transcript: str = Field(min_length=1, max_length=500000)
    meeting: MeetingInput | None = None
    frames: list[str] = Field(max_length=100)

    @field_validator("frames")
    @classmethod
    def validate_frames(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith(("https://", "http://")):
                raise ValueError("Frame URLs must use http or https.")
        return values


class SummarizeMeetingResponse(ContractModel):
    done: bool


class ActionItem(ContractModel):
    task: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=200)
    due: str | None = Field(default=None, max_length=100)


class MeetingMinutes(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=10000)
    decisions: list[str] = Field(default_factory=list, max_length=100)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=100)


class DiscussionTopic(ContractModel):
    topic: str = Field(min_length=1, max_length=300)
    details: str = Field(min_length=1, max_length=10000)
    conclusions: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)


class DetailedMeetingMinutes(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    meeting_objective: str = Field(min_length=1, max_length=3000)
    overview: str = Field(min_length=1, max_length=20000)
    discussion_topics: list[DiscussionTopic] = Field(default_factory=list, max_length=30)
    decisions: list[str] = Field(default_factory=list, max_length=100)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    open_questions: list[str] = Field(default_factory=list, max_length=100)
    follow_up_plan: list[str] = Field(default_factory=list, max_length=100)


class ExecutiveMeetingSummary(MeetingMinutes):
    summary: str = Field(min_length=1, max_length=1200)
    decisions: list[str] = Field(default_factory=list, max_length=8)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=8)


MeetingRequest = Annotated[
    AnalyzeRoomRequest | SummarizeMeetingRequest,
    Field(discriminator="tool"),
]

_REQUEST_ADAPTER: TypeAdapter[MeetingRequest] = TypeAdapter(MeetingRequest)


def parse_request(raw: str | dict[str, object]) -> AnalyzeRoomRequest | SummarizeMeetingRequest:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    return _REQUEST_ADAPTER.validate_python(payload)
