# Agent Instructions

This project was built with the microsoft-foundry skill. Before working on or answering questions about Foundry agents, read the microsoft-foundry skill first.

- Preserve the `analyze_room` and `summarize_meeting` JSON contracts in `meeting_agent/contracts.py`.
- Keep Azure resource access keyless through `DefaultAzureCredential` and scoped RBAC.
- Never commit `.azure`, `.env`, OAuth credentials, tokens, transcripts, or generated meeting documents.
- Run Ruff, mypy, pytest, Bicep build, and `azd ai agent doctor` after relevant changes.