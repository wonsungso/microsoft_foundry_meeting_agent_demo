from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_azure_ai.agents.hosting import ResponsesHostServer  # type: ignore[import-untyped]
from langchain_azure_ai.tools import AzureAIProjectToolbox  # type: ignore[import-untyped]
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .mail import LocalOutboxMailer, SummaryMailer, ToolboxMailer
from .storage import BlobMinutesRepository, LocalMinutesRepository, MinutesRepository

_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


def build_chat_model() -> ChatOpenAI:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini")
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, _AZURE_AI_SCOPE)
    return ChatOpenAI(
        model=deployment,
        base_url=str(openai_client.base_url),
        api_key=token_provider,
    )


async def load_toolbox_tools() -> list[BaseTool]:
    toolbox_name = os.environ["TOOLBOX_NAME"]
    tools = cast(
        list[BaseTool],
        await AzureAIProjectToolbox(toolbox_name=toolbox_name).get_tools(),
    )
    print(f"Loaded {len(tools)} tool(s) from Foundry toolbox '{toolbox_name}'.")
    return tools


def build_repository() -> MinutesRepository:
    artifacts = Path(os.environ.get("LOCAL_ARTIFACTS_DIR", "artifacts"))
    if os.environ.get("STORAGE_MODE", "fake").lower() == "live":
        return BlobMinutesRepository(
            os.environ["AZURE_STORAGE_ACCOUNT_URL"],
            os.environ.get("MEETING_MINUTES_CONTAINER", "meeting-minutes"),
        )
    return LocalMinutesRepository(artifacts / "minutes")


def build_mailer() -> SummaryMailer:
    artifacts = Path(os.environ.get("LOCAL_ARTIFACTS_DIR", "artifacts"))
    if os.environ.get("MAIL_MODE", "fake").lower() == "live":
        return ToolboxMailer(tools_loader=load_toolbox_tools)
    return LocalOutboxMailer(artifacts / "mail-outbox")


def run_server(graph: object) -> None:
    ResponsesHostServer(graph).run(port=int(os.environ.get("PORT", "8088")))