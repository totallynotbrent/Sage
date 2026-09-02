---
title: Learning Loop
description: "The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete."
---
# Learning Loop

The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete.

```mermaid
flowchart LR
    A["1. Study files<br/>.pdf .docx .pptx .md .markdown .txt"] --> B["2. Extract + chunk<br/>CHUNK_CHARS=1500, CHUNK_OVERLAP=200"]
    B --> C["3. Searchable chunks<br/>file status: ready / partial / failed"]
    C --> D["4. Session<br/>goal + selected files + grounding mode"]
    D --> E{"5. Diagnostic probe?<br/>(optional)"}
    E -->|"no — go straight to plan"| H
    E -->|"yes"| F["Probe — 3 adaptive questions<br/>server appends 'I don't know' option"]
    F --> G["Mastery evidence<br/>recorded per answer"]
    G --> H["6. Learning plan<br/>dependency-aware plan nodes"]
    H --> I["7. Teach each node<br/>streaming grounded chat + citations"]
    I --> J{"8. Periodic check<br/>check_due after 2 nodes"}
    J -->|"correct"| I
    J -->|"incorrect / idk"| K["9. Remediation<br/>hint / reveal / chat → Continue"]
    K -->|"POST /continue"| I
    J -->|"skip question"| H
    I -->|"no pending nodes"| L["10. Complete<br/>summary + read-only history"]
    D --> M["Grounded chat tutor<br/>retrieves from session files<br/>strict mode = sources required"]
    C --> M
    G --> I
    G --> J
```

## Latency-weighted mastery signal

Every quiz answer carries a `latency_ms` stamp: the UI records `performance.now()`
when the answer card renders and sends the retrieval time with the answer (as a
`[<n>ms]` badge in the graded chat reply, or as `latency_ms` in the review-grade
JSON). When absent, the server falls back to the `created_at → answered_at` gap,
and pre-existing rows are backfilled from those timestamps on migration to schema
version 4.

The stamp feeds three scheduling layers, so retrieval effort counts alongside
accuracy:

- **Mastery (Laplace confidence):** answers bucket into
  `fast-correct / slow-correct / fast-wrong / slow-wrong` (8s fast/slow cutoff).
  A slow-correct or slow-wrong adds an implicit observed event, so an effortful
  recall is weaker than it looks and an effortful miss schedules hardest —
  producing the monotonic ladder fast-correct > slow-correct > fast-wrong >
  slow-wrong.
- **Adaptive question count:** the acing branch (recent corrects + confidence
  ≥ 0.6 → one light check) now also requires fast corrects; slow recent corrects
  keep the standard two-question check.
- **FSRS review cards:** a slow-but-correct first recall seeds a lower initial
  stability (×0.6, floored at 1 day), so the card's stability-derived intervals
  shrink and it returns sooner.

## See also
- [[01-system-architecture|System Architecture]]
- [[03-session-state-machine|Session State Machine]]
- [[08-latex-notes|LaTeX Notes]]
