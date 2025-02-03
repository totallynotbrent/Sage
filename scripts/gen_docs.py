#!/usr/bin/env python3
"""Generate Sage Quartz content: index + per-diagram pages with embedded mermaid.

Reads the 8 `.mmd` diagram sources in docs/ and emits Quartz markdown pages
(embedded ```mermaid fences) plus an index.md with [[wikilinks]] so the graph
edges populate. Operational markdown (setup/ui/security/environment/testing) is
kept as-is and linked from the index.

Run from repo root: .venv/bin/python scripts/gen_docs.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS  # pages are emitted alongside the .mmd sources (Quartz builds -d ../docs)

DIAGRAMS = [
    {
        "mmd": "01-system-architecture.mmd",
        "slug": "01-system-architecture",
        "title": "System Architecture",
        "desc": "High-level Sage architecture: study files, chunking, retrieval, session loop, streaming chat, and the data model.",
        "see_also": ["02-learning-loop", "06-data-model-er"],
    },
    {
        "mmd": "02-learning-loop.mmd",
        "slug": "02-learning-loop",
        "title": "Learning Loop",
        "desc": "The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete.",
        "see_also": ["01-system-architecture", "03-session-state-machine", "08-latex-notes"],
    },
    {
        "mmd": "03-session-state-machine.mmd",
        "slug": "03-session-state-machine",
        "title": "Session State Machine",
        "desc": "Session phases and the transitions between them.",
        "see_also": ["02-learning-loop", "07-ui-screens"],
    },
    {
        "mmd": "04-complete-ui-flow-sequence.mmd",
        "slug": "04-complete-ui-flow-sequence",
        "title": "Complete UI Flow (sequence)",
        "desc": "Sequence diagram of the recommended complete web UI flow.",
        "see_also": ["07-ui-screens", "05-streaming-chat-sse"],
    },
    {
        "mmd": "05-streaming-chat-sse.mmd",
        "slug": "05-streaming-chat-sse",
        "title": "Streaming Chat & SSE",
        "desc": "SSE event flows for streaming chat: /turns, /retry, /stop, citations, heartbeats.",
        "see_also": ["01-system-architecture", "04-complete-ui-flow-sequence", "03-session-state-machine"],
    },
    {
        "mmd": "06-data-model-er.mmd",
        "slug": "06-data-model-er",
        "title": "Data Model (ER)",
        "desc": "SQLite data model: files, chunks, sessions, plan nodes, quiz, messages, mastery, feedback.",
        "see_also": ["01-system-architecture", "02-learning-loop"],
    },
    {
        "mmd": "07-ui-screens.mmd",
        "slug": "07-ui-screens",
        "title": "UI Screens",
        "desc": "Suggested web UI screens and navigation between them.",
        "see_also": ["04-complete-ui-flow-sequence", "03-session-state-machine"],
    },
    {
        "mmd": "08-latex-notes.mmd",
        "slug": "08-latex-notes",
        "title": "LaTeX Notes",
        "desc": "LaTeX-notes integration: folder watch, same-stem pairing, notes quiz.",
        "see_also": ["02-learning-loop", "06-data-model-er"],
    },
]

OPERATIONAL = [
    ("setup", "Setup & deployment", "Python/uvicorn/systemd, LAN & Tailscale access"),
    ("ui", "Web UI", "Streaming chat, artifacts, check cards, confidence, file viewer"),
    ("security", "Security", "Trusted-network-only deployment"),
    ("environment", "Environment variables", "BROT_* / SEARXNG_URL / HOST / PORT / limits"),
    ("testing", "Testing", "pytest suite, wheel preflight, live smoke test"),
]


def _sections(code: str) -> list[str]:
    parts = re.split(r"^%% .*$", code, flags=re.M)
    return [p.strip() for p in parts if p.strip()]


def wrap(title: str, desc: str, code: str, see_also: list[str]) -> str:
    sections = _sections(code) if len(_sections(code)) > 1 else [code.strip()]
    mermaid = "\n\n".join(f"```mermaid\n{section}\n```" for section in sections)
    links = "\n".join(f"- [[{s}]]" for s in see_also)
    return (
        f"---\ntitle: {title}\ndescription: \"{desc}\"\n---\n"
        f"# {title}\n\n{desc}\n\n{mermaid}\n\n"
        f"## See also\n{links}\n"
    )


def main() -> None:
    for d in DIAGRAMS:
        code = (DOCS / d["mmd"]).read_text()
        page = wrap(d["title"], d["desc"], code, d["see_also"])
        (OUT / f"{d['slug']}.md").write_text(page)
        print(f"wrote {d['slug']}.md")

    operational_links = "\n".join(f"- [[{slug}|{title}]] — {desc}" for slug, title, desc in OPERATIONAL)
    diagram_links = "\n".join(f"- [[{d['slug']}|{d['title']}]] — {d['desc']}" for d in DIAGRAMS)
    index = (
        "---\ntitle: Sage\ndescription: \"Sage — an adaptive AI tutoring assistant\"\n---\n"
        "# Sage\n\nAn adaptive, evidence-based AI tutor. Sage ingests your study files, chunks them, "
        "and runs a session loop (probe → plan → teach → check → remediate → complete) with FSRS "
        "spaced repetition, confidence calibration, and learner-generated questions. This site "
        "documents the architecture and operations.\n\n"
        "## Diagrams\n\n"
        "Start with [[01-system-architecture|System Architecture]], then the "
        "[[02-learning-loop|Learning Loop]].\n\n"
        f"{diagram_links}\n\n## Operations\n\n{operational_links}\n"
    )
    (OUT / "index.md").write_text(index)
    print("wrote index.md")


if __name__ == "__main__":
    main()