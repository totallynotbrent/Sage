# Sage — Documentation Index

## Mermaid diagrams

Diagrams generated from `ui.txt` (the Sage web UI notes). Preview any `.mmd`
file on [mermaid.live](https://mermaid.live), with a VS Code Mermaid extension,
or directly on GitHub (which renders `.mmd` natively).

| File | Diagram | Covers (ui.txt sections) |
| ---- | ------- | ------------------------ |
| [01-system-architecture.mmd](01-system-architecture.mmd) | Flowchart — system architecture | Purpose, deployment facts, security |
| [02-learning-loop.mmd](02-learning-loop.mmd) | Flowchart — end-to-end learning loop | Purpose (study files → chunks → session → probe → plan → teach → check → remediate → complete) |
| [03-session-state-machine.mmd](03-session-state-machine.mmd) | State diagram — session phases | Session phases, State transition summary |
| [04-complete-ui-flow-sequence.mmd](04-complete-ui-flow-sequence.mmd) | Sequence — complete recommended UI flow | Recommended complete UI flow (A–E) |
| [05-streaming-chat-sse.mmd](05-streaming-chat-sse.mmd) | Sequence — SSE chat events | Streaming chat (`/turns`, `/retry`, `/stop`, citations, heartbeats) |
| [06-data-model-er.mmd](06-data-model-er.mmd) | ER diagram — SQLite data model | Files, chunks, sessions, plan nodes, quiz, messages, mastery, preferences, feedback |
| [07-ui-screens.mmd](07-ui-screens.mmd) | Flowchart — screen navigation | Suggested screens 1–9 |

Common cross-cutting details (error envelope, status codes, data consistency
rules for the frontend) are kept out of the diagrams; see `ui.txt` for those.

## Operational docs

| Doc | Covers |
| --- | ------ |
| [setup.md](setup.md) | Python 3.11 setup (Bookworm vs Trixie), `.env`, run.sh / run.bat, uvicorn, LAN access |
| [security.md](security.md) | Trusted-network-only warning |
| [environment.md](environment.md) | Environment variable reference (BROT_* / HOST / PORT / limits) |
| [testing.md](testing.md) | pytest suite, wheel preflight, live endpoint smoke test |
