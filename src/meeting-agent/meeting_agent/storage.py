from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .contracts import DetailedMeetingMinutes, MeetingMinutes


@dataclass(frozen=True)
class MinutesArtifact:
    name: str
    url: str


class MinutesRepository(Protocol):
    async def save(
        self,
        minutes: DetailedMeetingMinutes | MeetingMinutes,
        frames: list[str],
        transcript: str | None = None,
    ) -> MinutesArtifact: ...


def _safe_filename(title: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", title).strip("-") or "meeting"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{normalized[:80]}.md"


def _list_items(values: list[str], empty_message: str) -> list[str]:
    return [f"- {value}" for value in values] or [f"- {empty_message}"]


def render_markdown(
    minutes: DetailedMeetingMinutes | MeetingMinutes,
    frames: list[str],
    transcript: str | None = None,
) -> bytes:
    decisions = [f"- {decision}" for decision in minutes.decisions]
    if not decisions:
        decisions = ["- 명시적으로 기록된 결정 사항이 없습니다."]

    action_items: list[str] = []
    for item in minutes.action_items:
        details = [item.task]
        if item.owner:
            details.append(f"담당: {item.owner}")
        if item.due:
            details.append(f"기한: {item.due}")
        action_items.append(f"- [ ] {' | '.join(details)}")
    if not action_items:
        action_items = ["- 명시적으로 기록된 후속 조치가 없습니다."]

    visual_references = [f"- {frame}" for frame in frames]
    if not visual_references:
        visual_references = ["- 제공된 시각 자료가 없습니다."]

    if isinstance(minutes, DetailedMeetingMinutes):
        discussions: list[str] = []
        for index, topic in enumerate(minutes.discussion_topics, start=1):
            discussions.extend(
                [
                    f"### {index}. {topic.topic}",
                    "",
                    topic.details,
                    "",
                    "#### 논의 결론",
                    *_list_items(topic.conclusions, "명시적으로 합의된 결론이 없습니다."),
                    "",
                    "#### 주제별 미해결 질문",
                    *_list_items(topic.open_questions, "남은 질문이 없습니다."),
                    "",
                ]
            )
        if not discussions:
            discussions = ["- 구조화된 상세 논의가 없습니다.", ""]

        markdown = "\n".join(
            [
                f"# {minutes.title}",
                "",
                f"- **생성 시각(UTC)**: {datetime.now(UTC).isoformat()}",
                "",
                "## 회의 목적",
                minutes.meeting_objective,
                "",
                "## 전체 개요",
                minutes.overview,
                "",
                "## 상세 논의",
                *discussions,
                "## 결정 사항",
                *decisions,
                "",
                "## 후속 조치",
                *action_items,
                "",
                "## 리스크 및 주의 사항",
                *_list_items(minutes.risks, "명시적으로 기록된 리스크가 없습니다."),
                "",
                "## 미해결 질문",
                *_list_items(minutes.open_questions, "미해결 질문이 없습니다."),
                "",
                "## 후속 회의 계획",
                *_list_items(minutes.follow_up_plan, "별도 후속 계획이 없습니다."),
                "",
                "## 시각 자료",
                *visual_references,
                "",
                "## 원문 기록",
                transcript or "제공된 원문 기록이 없습니다.",
                "",
            ]
        )
        return markdown.encode("utf-8")

    markdown = "\n".join(
        [
            f"# {minutes.title}",
            "",
            f"- **생성 시각(UTC)**: {datetime.now(UTC).isoformat()}",
            "",
            "## 회의 요약",
            minutes.summary,
            "",
            "## 결정 사항",
            *decisions,
            "",
            "## 후속 조치",
            *action_items,
            "",
            "## 시각 자료",
            *visual_references,
            "",
        ]
    )
    return markdown.encode("utf-8")


class LocalMinutesRepository:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def save(
        self,
        minutes: DetailedMeetingMinutes | MeetingMinutes,
        frames: list[str],
        transcript: str | None = None,
    ) -> MinutesArtifact:
        name = _safe_filename(minutes.title)
        path = self._output_dir / name

        def write() -> None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(render_markdown(minutes, frames, transcript))

        await asyncio.to_thread(write)
        return MinutesArtifact(name=name, url=path.resolve().as_uri())


class BlobMinutesRepository:
    def __init__(self, account_url: str, container_name: str) -> None:
        self._account_url = account_url.rstrip("/")
        self._container_name = container_name
        self._client = BlobServiceClient(
            account_url=self._account_url,
            credential=DefaultAzureCredential(),
        )

    async def save(
        self,
        minutes: DetailedMeetingMinutes | MeetingMinutes,
        frames: list[str],
        transcript: str | None = None,
    ) -> MinutesArtifact:
        name = _safe_filename(minutes.title)
        payload = render_markdown(minutes, frames, transcript)

        def upload() -> None:
            blob = self._client.get_blob_client(self._container_name, name)
            blob.upload_blob(
                payload,
                overwrite=False,
                content_settings=ContentSettings(
                    content_type="text/markdown; charset=utf-8"
                ),
                metadata={"source": "ai-glasses-meeting-agent"},
            )

        await asyncio.to_thread(upload)
        return MinutesArtifact(
            name=name,
            url=f"{self._account_url}/{self._container_name}/{name}",
        )
