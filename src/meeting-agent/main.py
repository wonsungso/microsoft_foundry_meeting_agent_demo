"""Triage Hosted Agent entry point for the AI Glasses demo."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from meeting_agent.remote import FoundryAgentClient
from meeting_agent.runtime import run_server
from meeting_agent.triage import TriageWorkflow

load_dotenv()


def main() -> None:
    client = FoundryAgentClient(os.environ["FOUNDRY_PROJECT_ENDPOINT"])
    graph = TriageWorkflow(
        client=client,
        context_agent=os.environ.get("CONTEXT_AGENT_NAME", "context-agent"),
        meeting_notes_agent=os.environ.get("MEETING_AGENT_NAME", "meeting-agent"),
        summarizer_agent=os.environ.get("SUMMARIZER_AGENT_NAME", "summarizer-agent"),
        notify_agent=os.environ.get("NOTIFY_AGENT_NAME", "notify-agent"),
        recipient=os.environ.get("MAIL_RECIPIENT", "demo@example.com"),
    ).compile()
    run_server(graph)


if __name__ == "__main__":
    main()
