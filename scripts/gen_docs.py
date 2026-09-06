#!/usr/bin/env python3
"""Generate Sage Quartz content: index + the LaTeX-notes page.

Reads the LaTeX-notes `.mmd` diagram source and emits an embedded-mermaid
Quartz page, plus an `index.md` with [[wikilinks]] so the Obsidian-style graph
edges populate. The other public pages (how-Sage-teaches, setup, ui, security,
environment) are hand-written prose in `docs/` and are only linked here, not
generated. Internal architecture/state-machine/SSE/data-model diagram pages
were taken off the public site (2026-09-05, user directive): only use it for
the user-facing LaTeX-notes feature page and the index.

Run from repo root: .venv/bin/python scripts/gen_docs.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS  # pages are emitted alongside the .mmd sources (Quartz builds -d ../docs)

DIAGRAMS = [
    {
        "mmd": "08-latex-notes.mmd",
        "slug": "08-latex-notes",
        "title": "LaTeX notes",
        "desc": "LaTeX-notes integration: folder watch, same-stem pairing, notes quiz.",
        "see_also": ["02-learning-loop"],
    },
]

PROSE = [
    ("02-learning-loop", "How Sage teaches",
     "The tutoring arc: probe, planning, dense teaching, a comprehensive final quiz, and re-teaching what didn't stick."),
    ("08-latex-notes", "LaTeX notes",
     "Point Sage at a LaTeX notes folder and it ingests, pairs, and quizzes over your notes."),
    ("setup", "Setup & deployment",
     "Requirements, the .env file, one-command start, and LAN & Tailscale access."),
    ("environment", "Environment variables",
     "API_URL / API_KEY / MODEL, SEARXNG_URL, HOST, PORT, and the limits."),
    ("ui", "Using the web UI",
     "Streaming chat, answer buttons, and the artifacts rail."),
    ("security", "Security",
     "Trusted-network-only deployment, the watch API, and study-file prompt injection."),
]


def _sections(code: str) -> list[str]:
    parts = code.split("\n%% ")
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
        code = (DOCS / d["mmd"]).read_text()
        page = wrap(d["title"], d["desc"], code, d["see_also"])
        (OUT / f"{d['slug']}.md").write_text(page)
        print(f"wrote {d['slug']}.md")

    learn = "\n".join(
        f"- [[{slug}|{title}]] — {desc}" for slug, title, desc in PROSE
        if slug in ("02-learning-loop", "08-latex-notes")
    )
    operate = "\n".join(
        f"- [[{slug}|{title}]] — {desc}" for slug, title, desc in PROSE
        if slug not in ("02-learning-loop", "08-latex-notes")
    )
    index = (
        "---\ntitle: Sage\ndescription: \"Sage — an adaptive AI tutoring assistant\"\n---\n"
        "# Sage\n\nSage is an adaptive AI tutor. It ingests your study files, teaches you "
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