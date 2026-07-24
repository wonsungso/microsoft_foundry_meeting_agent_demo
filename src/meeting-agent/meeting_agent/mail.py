from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from .contracts import MeetingMinutes
from .storage import MinutesArtifact

logger = logging.getLogger(__name__)


class SummaryMailer(Protocol):
    async def send(
        self,
        recipient: str,
        minutes: MeetingMinutes,
        artifact: MinutesArtifact,
    ) -> bool: ...


def _mail_prompt(recipient: str, minutes: MeetingMinutes, artifact: MinutesArtifact) -> str:
    return (
        "Send an Outlook email now. Do not only draft or describe the email.\n"
        f"To: {recipient}\n"
        f"Subject: {_mail_subject(minutes)}\n\n"
        f"{_mail_body(minutes, artifact)}"
    )


def _mail_subject(minutes: MeetingMinutes) -> str:
    return f"[AI Glasses] {minutes.title} 회의 요약"


def _mail_body(minutes: MeetingMinutes, artifact: MinutesArtifact) -> str:
    decisions = "\n".join(f"- {item}" for item in minutes.decisions) or "- 없음"
    action_items = "\n".join(
        "- " + " | ".join(
            value
            for value in (
                item.task,
                f"담당: {item.owner}" if item.owner else None,
                f"기한: {item.due}" if item.due else None,
            )
            if value
        )
        for item in minutes.action_items
    ) or "- 없음"
    return (
        f"핵심 요약\n{minutes.summary}\n\n"
        f"주요 결정\n{decisions}\n\n"
        f"우선 후속 조치\n{action_items}\n\n"
        f"전체 상세 회의록\n{artifact.url}"
    )


class LocalOutboxMailer:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def send(
        self,
        recipient: str,
        minutes: MeetingMinutes,
        artifact: MinutesArtifact,
    ) -> bool:
        message = {
            "recipient": recipient,
            "subject": _mail_subject(minutes),
            "body": _mail_prompt(recipient, minutes, artifact),
            "created_at": datetime.now(UTC).isoformat(),
        }
        path = self._output_dir / f"{artifact.name}.mail.json"

        def write() -> None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")

        await asyncio.to_thread(write)
        return True


class ToolboxMailer:
    def __init__(
        self,
        tools: Sequence[BaseTool] | None = None,
        *,
        tools_loader: Callable[[], Awaitable[list[BaseTool]]] | None = None,
    ) -> None:
        if tools is None and tools_loader is None:
            raise ValueError("ToolboxMailer requires tools or a tools loader.")
        self._tools = list(tools) if tools is not None else None
        self._tools_loader = tools_loader

    async def _get_tools(self) -> list[BaseTool]:
        if self._tools is None:
            if self._tools_loader is None:
                raise RuntimeError("The Foundry Toolbox tools loader is unavailable.")
            self._tools = await self._tools_loader()
        return self._tools

    @staticmethod
    def _find_tool(tools: Sequence[BaseTool], suffix: str) -> BaseTool | None:
        for tool in tools:
            normalized_name = re.sub(r"[^a-z0-9]", "", tool.name.lower())
            if normalized_name.endswith(suffix):
                return tool
        return None

    @staticmethod
    def _select_tool(tools: Sequence[BaseTool]) -> BaseTool:
        fallback: BaseTool | None = None
        for tool in tools:
            normalized_name = re.sub(r"[^a-z0-9]", "", tool.name.lower())
            if "graphmailsendmail" in normalized_name:
                logger.info("Selected Outlook Mail send tool: %s", tool.name)
                return tool
            if normalized_name.endswith(("sendmail", "sendemail", "sendemailwithattachments")):
                fallback = tool
        if fallback is not None:
            logger.info("Selected Outlook Mail send tool: %s", fallback.name)
            return fallback
        raise RuntimeError("The Foundry Toolbox does not expose an Outlook Mail send action.")

    @staticmethod
    def _properties(tool: BaseTool) -> dict[str, Any]:
        args_schema = tool.args_schema
        if isinstance(args_schema, dict):
            schema = args_schema
        elif args_schema is not None and hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        elif args_schema is not None and hasattr(args_schema, "schema"):
            schema = args_schema.schema()
        else:
            schema = {}
        properties = schema.get("properties", {})
        return properties if isinstance(properties, dict) else {}

    @staticmethod
    def _message_arguments(
        tool: BaseTool,
        recipient: str,
        minutes: MeetingMinutes,
        artifact: MinutesArtifact,
    ) -> dict[str, Any]:
        properties = ToolboxMailer._properties(tool)
        logger.info("Outlook Mail action %s parameters: %s", tool.name, sorted(properties))
        subject = _mail_subject(minutes)
        content = _mail_body(minutes, artifact)
        recipients = [{"emailAddress": {"address": recipient}}]
        message = {
            "subject": subject,
            "toRecipients": recipients,
            "body": {"contentType": "Text", "content": content},
        }
        values: dict[str, Any] = {
            "message": message,
            "emailMessage": message,
            "subject": subject,
            "toRecipients": recipients,
            "recipients": [recipient],
            "recipientEmails": [recipient],
            "to": recipient,
            "recipient": recipient,
            "emailAddress": recipient,
            "body": content,
            "saveToSentItems": True,
            "preferHtml": False,
        }
        for name in ("to", "recipient", "emailAddress"):
            definition = properties.get(name)
            if isinstance(definition, dict) and definition.get("type") == "array":
                values[name] = [recipient]
        arguments = {name: values[name] for name in properties if name in values}
        recipient_parameters = {
            "message",
            "emailMessage",
            "toRecipients",
            "recipients",
            "recipientEmails",
            "to",
            "recipient",
            "emailAddress",
        }
        if not recipient_parameters.intersection(arguments):
            raise RuntimeError(
                "Outlook Mail action has no supported recipient parameter: "
                f"{sorted(properties)}"
            )
        return arguments

    @staticmethod
    def _succeeded(result: Any) -> bool:
        if result is None:
            return False
        serialized = result if isinstance(result, str) else json.dumps(result, default=str)
        lowered = serialized.lower()
        return "error executing tool" not in lowered and '"iserror": true' not in lowered

    @staticmethod
    def _message_id(result: Any) -> str | None:
        if isinstance(result, dict):
            message_id = result.get("messageId")
            if isinstance(message_id, str):
                return message_id
            for key in ("data", "text", "content"):
                nested = ToolboxMailer._message_id(result.get(key))
                if nested:
                    return nested
            return None
        if isinstance(result, list):
            for item in result:
                message_id = ToolboxMailer._message_id(item)
                if message_id:
                    return message_id
            return None
        if isinstance(result, str):
            try:
                return ToolboxMailer._message_id(json.loads(result))
            except json.JSONDecodeError:
                return None
        return None

    async def send(
        self,
        recipient: str,
        minutes: MeetingMinutes,
        artifact: MinutesArtifact,
    ) -> bool:
        tools = await self._get_tools()
        create_draft = self._find_tool(tools, "createdraftmessage")
        send_draft = self._find_tool(tools, "senddraftmessage")
        if create_draft is not None and send_draft is not None:
            create_arguments = self._message_arguments(
                create_draft, recipient, minutes, artifact
            )
            logger.info(
                "Invoking %s with parameters: %s",
                create_draft.name,
                sorted(create_arguments),
            )
            create_result = await create_draft.ainvoke(create_arguments)
            message_id = self._message_id(create_result)
            if message_id is None:
                raise RuntimeError("Outlook Mail draft creation did not return a message ID.")
            send_properties = self._properties(send_draft)
            id_values = {"id": message_id, "messageId": message_id, "draftId": message_id}
            send_arguments = {
                name: id_values[name] for name in send_properties if name in id_values
            }
            if not send_arguments:
                raise RuntimeError(
                    "Outlook Mail send-draft action has no supported ID parameter: "
                    f"{sorted(send_properties)}"
                )
            logger.info(
                "Invoking %s with parameters: %s", send_draft.name, sorted(send_arguments)
            )
            return self._succeeded(await send_draft.ainvoke(send_arguments))

        tool = self._select_tool(tools)
        arguments = self._message_arguments(tool, recipient, minutes, artifact)
        logger.info("Invoking %s with parameters: %s", tool.name, sorted(arguments))
        return self._succeeded(await tool.ainvoke(arguments))
