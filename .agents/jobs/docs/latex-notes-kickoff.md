# Sage — LaTeX-Notes Integration: Kickoff Intake

Date: 2026-08-18 · Feature: backend LaTeX notes grounding + document-grounded testing · UI deferred.

## Goal

Extend Sage (the local-first AI tutor) so the user's LaTeX notes — `.tex` source compiled to PDF — drive both grounded Q&A and testing: "when I ask it questions it can look at my documents and test me based on that."

## Intake answers (user-confirmed)

1. **Ingestion format**: Both `.tex` + matched PDF. `.tex` is the source of truth (lossless structure/math); a same-basename PDF is paired for page numbers.
2. **Intake path**: API upload + folder watch. New backend feature: point Sage at a notes directory (on the NAS) it scans/indexes automatically.
3. **PDF without .tex**: Extract with warning (partial status, degraded-math caveat) — never reject.
4. **Math in question/answer text**: Unicode math (α, ∫). Raw LaTeX stays only in source blocks (data, not parsed/emitted).
5. **Session flow**: Both modes — full probe → plan → teach → check flow grounded in the notes, AND ad-hoc chat + on-demand quiz.
6. **Custom macros**: Mostly standard LaTeX (no heavy custom `\newcommand` handling; basic robustness only: comments, verbatim, nested braces).
7. **Corpus size**: Hundreds+ files — per-subject organization and stricter retrieval matter.
8. **Deployment**: Server on Raspberry Pi (ARM64); notes live on the NAS (\\raspberrypi\brentNAS\...) — the folder watch is a local path on the Pi.
9. **Acceptance**: Both (a) cited answers pointing at the right section/equation and (b) quiz questions demonstrably sourced from the notes' content.

## Deferred (recommended, user can pull forward later)

- Dense embeddings (BM25 exact-match suffices at this scale; check `/embeddings` on the endpoint before committing).
- FSRS spaced-repetition scheduling across sessions (evidence_json already collects the data).
- IRT/BKT mastery models (wrong for one user, sparse data; keep Laplace proportion).
- Verification/fact-checking subagents (notes are self-authored, high-trust; grounding + source_ref is v1 safety).
- All UI work.

## Research basis

Hermes T16 research log (`.agents/jobs/T16.json`): pylatexenc for .tex parsing; .tex-first + PDF-pairing architecture; verified math-tokenizer damage in `retrieval.py` (`[a-z0-9']+` strips all math); KNIGHT/CPAL criteria for document-grounded MCQ generation; SQLite FTS5 as the later BM25 upgrade; unicode-math JSON safety vs raw LaTeX escapes.

## Key gaps the feature closes

1. No `.tex` extractor in the registry (`app/services/extraction/`).
2. Math-blind retrieval tokenizer.
3. Quiz questions not pinned to specific note content (no provenance).
4. No automatic notes ingestion (upload-only today).
