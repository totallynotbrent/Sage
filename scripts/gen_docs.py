#!/usr/bin/env python3
"""Generate Sage Quartz content: index + the LaTeX-notes page.

The LaTeX-notes mermaid source is inlined below, then wrapped into a Quartz
page with an `index.md` of [[wikilinks]] so the Obsidian-style graph edges
populate. The other public pages (how-Sage-teaches, setup, ui, security,
environment) are hand-written prose in `docs/` and are only linked here, not
generated.

Run from repo root: .venv/bin/python scripts/gen_docs.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS  # pages are emitted into docs/ (Quartz builds -d ../docs)

DIAGRAMS = [
    {
        "slug": "08-latex-notes",
        "title": "LaTeX notes",
        "desc": "LaTeX-notes integration: folder watch, same-stem pairing, notes quiz.",
        "see_also": ["02-learning-loop"],
        "mmd": """%% LaTeX-notes integration — 1. folder watch
flowchart LR
    NOTES["Notes folders (SAGE_WATCH_DIRS)<br/>subject/ subfolders · .tex + .pdf"]
    W["app/services/watcher.py<br/>watcher_loop (lifespan, every SAGE_WATCH_SCAN_SECONDS)<br/>scan_once · sync_watch_sources"]
    FILES[("files")]
    CHUNKS[("chunks")]
    TXT["extraction/tex.py<br/>pylatexenc · sections · environments<br/>display math · preamble macros"]
    FILE_SVC["FileService.ingest_from_disk<br/>subject = first path segment"]

    NOTES -->|"hash each allowed file"| W
    W --> FILE_SVC
    FILE_SVC --> TXT
    TXT --> CHUNKS
    W --> FILES
    NOTES -->|"new / changed / missing"| W
    W -->|"last_scan_at · last_error"| FILES

%% LaTeX-notes integration — 2. same-stem pairing (.tex source + .pdf pages)
flowchart LR
    A["calc.tex ingested"] --> P{"same-stem candidate?<br/>stem equal · suffix differs"}
    P -->|"yes"| B["paired_file_id set both ways<br/>expand_pairings pulls the pair's chunks<br/>pair=calc.pdf on DOC blocks"]
    P -->|"no"| C["paired_file_id = NULL"]
    D["delete one file"] --> E["survivor's paired_file_id nulled"]

%% LaTeX-notes integration — 3. notes quiz (grounded MCQ)
sequenceDiagram
    autonumber
    participant C as Client
    participant A as app/api/learning.py
    participant L as LearningService
    participant N as app/llm/notes.py
    participant DB as SQLite
    participant M as LLM

    C->>A: POST /api/sessions/{id}/notes-quiz {count, subject}
    A->>L: generate_notes_quiz(count, subject)
    L->>DB: ready chunks (pairings expanded)<br/>keep environment IS NOT NULL, subject-filtered
    Note over L,N: seed chunks become the seed blocks
    loop per seed chunk (up to count)
        L->>N: request_notes_questions (one seed block)
        N->>M: generate question (KNIGHT criteria · unicode-math rule)
        N->>M: verify_answerability → {"supported": bool}
        alt supported
            N-->>L: question + source_ref (chunk_id, file_id, section,<br/>environment, label, page)
        else unsupported / unparseable
            N-->>L: dropped
        end
    end
    L->>DB: insert quiz_questions (kind notes · source_ref JSON)
    L-->>C: {session, questions} — phase untouched

    C->>A: POST /api/sessions/{id}/quiz/{qid}/answer
    A->>L: answer_quiz (kind notes accepted)
    L->>DB: mastery evidence source "notes"
    Note over L: notes answers never change session phase
    L-->>C: {result, session} — phase unchanged""",
    },
]

PROSE = [
    ("02-learning-loop", "How Sage teaches",
     "the tutoring arc of probe, planning, dense teaching, a full final quiz, and re-teaching what didn't stick."),
    ("08-latex-notes", "LaTeX notes",
     "point Sage at a LaTeX notes folder and it ingests, pairs, and quizzes over your notes."),
    ("setup", "Setup & deployment",
     "requirements, the .env file, one-command start, and LAN & Tailscale access."),
    ("environment", "Environment variables",
     "API_URL / API_KEY / MODEL, SEARXNG_URL, HOST, PORT, and the limits."),
    ("ui", "Using the web UI",
     "streaming chat, answer cards, and the file source viewer."),
    ("security", "Security",
     "trusted-network-only deployment and the password gate."),
]


def _sections(code: str) -> list[str]:
    parts = re.split(r"^%% .*$", code, flags=re.M)
    return [p.strip() for p in parts if p.strip()]


def wrap(title: str, desc: str, code: str, see_also: list[str]) -> str:
    sections = _sections(code) if len(_sections(code)) > 1 else [code.strip()]
    mermaid = "\n\n".join(f"```mermaid\n{section}\n```" for section in sections)
    by_slug = {p[0]: p[1] for p in PROSE}
    links = "\n".join(f"- [[{s}|{by_slug[s]}]]" for s in see_also if s in by_slug)
    return (
        f"---\ntitle: {title}\ndescription: \"{desc}\"\n---\n"
        f"# {title}\n\n{desc}\n\n{mermaid}\n\n"
        f"## See also\n{links}\n"
    )


def main() -> None:
    for d in DIAGRAMS:
        code = d["mmd"]
        page = wrap(d["title"], d["desc"], code, d["see_also"])
        (OUT / f"{d['slug']}.md").write_text(page)
        print(f"wrote {d['slug']}.md")

    learn = "\n".join(
        f"- [[{slug}|{title}]]: {desc}" for slug, title, desc in PROSE
        if slug in ("02-learning-loop", "08-latex-notes")
    )
    operate = "\n".join(
        f"- [[{slug}|{title}]]: {desc}" for slug, title, desc in PROSE
        if slug not in ("02-learning-loop", "08-latex-notes")
    )
    index = (
        "---\ntitle: Sage\ndescription: \"Sage, an adaptive AI tutoring assistant\"\n---\n"
        "# Sage\n\n"
        "> **This project is not complete.** Sage is under active development and has rough\n"
        "> edges. Things that are still being built or are known to be unfinished include:\n"
        "> the PDF source viewer (currently a collapsible side rail, still being polished),\n"
        "> in-text citation rendering, the automatic diagnostic probe that opens a session,\n"
        "> the plan-building and re-teaching flow, LaTeX rendering in every card, and the\n"
        "> final-quiz and grading loop. The UI changes often and the learning arc is still\n"
        "> being tuned. Expect breaking changes.\n\n"
        "Sage is an adaptive AI tutor. It ingests your study files, teaches you "
        "clearly, checks what stuck with a final quiz, and re-teaches the gaps. "
        "Run it yourself and use it for whatever you're studying.\n\n"
        "## Learning\n\n"
        f"{learn}\n\n"
        "## Run & operate\n\n"
        f"{operate}\n"
    )
    (OUT / "index.md").write_text(index)
    print("wrote index.md")


if __name__ == "__main__":
    main()