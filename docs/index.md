---
title: Sage
description: "Sage — an adaptive AI tutoring assistant"
---
# Sage

An adaptive, evidence-based AI tutor. Sage ingests your study files, chunks them, and runs a session loop (probe → plan → teach → check → remediate → complete) with FSRS spaced repetition, confidence calibration, and learner-generated questions. This site documents the architecture and operations.

## Diagrams

Start with [[01-system-architecture|System Architecture]], then the [[02-learning-loop|Learning Loop]].

- [[01-system-architecture|System Architecture]] — High-level Sage architecture: study files, chunking, retrieval, session loop, streaming chat, and the data model.
- [[02-learning-loop|Learning Loop]] — The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete.
- [[03-session-state-machine|Session State Machine]] — Session phases and the transitions between them.
- [[04-complete-ui-flow-sequence|Complete UI Flow (sequence)]] — Sequence diagram of the recommended complete web UI flow.
- [[05-streaming-chat-sse|Streaming Chat & SSE]] — SSE event flows for streaming chat: /turns, /retry, /stop, citations, heartbeats.
- [[06-data-model-er|Data Model (ER)]] — SQLite data model: files, chunks, sessions, plan nodes, quiz, messages, mastery, feedback.
- [[07-ui-screens|UI Screens]] — Suggested web UI screens and navigation between them.
- [[08-latex-notes|LaTeX Notes]] — LaTeX-notes integration: folder watch, same-stem pairing, notes quiz.

## Operations

- [[setup|Setup & deployment]] — Python/uvicorn/systemd, LAN & Tailscale access
- [[ui|Web UI]] — Streaming chat, artifacts, check cards, confidence, file viewer
- [[security|Security]] — Trusted-network-only deployment
- [[environment|Environment variables]] — API_URL / API_KEY / MODEL, SEARXNG_URL, HOST, PORT, limits
- [[testing|Testing]] — pytest suite, wheel preflight, live smoke test
