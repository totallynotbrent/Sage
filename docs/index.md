---
title: Sage
description: "Sage, an adaptive AI tutoring assistant"
---
# Sage

> **This project is not complete.** Sage is under active development and has rough
> edges. Things that are still being built or are known to be unfinished include:
> the PDF source viewer (currently a collapsible side rail, still being polished),
> in-text citation rendering, the automatic diagnostic probe that opens a session,
> the plan-building and re-teaching flow, LaTeX rendering in every card, and the
> final-quiz and grading loop. The UI changes often and the learning arc is still
> being tuned. Expect breaking changes.

Sage is an adaptive AI tutor. It ingests your study files, teaches you clearly, checks what stuck with a final quiz, and re-teaches the gaps. Run it yourself and use it for whatever you're studying.

## Learning

- [[02-learning-loop|How Sage teaches]]: the tutoring arc of probe, planning, dense teaching, a full final quiz, and re-teaching what didn't stick.
- [[08-latex-notes|LaTeX notes]]: point Sage at a LaTeX notes folder and it ingests, pairs, and quizzes over your notes.

## Run & operate

- [[setup|Setup & deployment]]: requirements, the .env file, one-command start, and LAN & Tailscale access.
- [[environment|Environment variables]]: API_URL / API_KEY / MODEL, SEARXNG_URL, HOST, PORT, and the limits.
- [[ui|Using the web UI]]: streaming chat, answer cards, and the file source viewer.
- [[security|Security]]: trusted-network-only deployment and the password gate.
