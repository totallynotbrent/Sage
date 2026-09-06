---
title: Security
---
# Security warning

**Trusted-network-only service.** Sage ships with no authentication. Anyone
who can reach the Pi's IP on your local network can call the API and use the
configured model endpoint. Only run it on a network you trust.

The API key stays server-side and is never delivered to the browser, and usage
is bounded by the single local endpoint. A shared access token is a planned
follow-up.

SearXNG (when configured) is queried server-side only; the browser never sees
SEARXNG_URL. See [[setup|Setup & deployment]] and [[environment|Environment
variables]] for how to configure a deployment.

## Watch API

The watch API (`POST /api/watch`, `POST /api/watch/scan`) reads server-local
files from operator-supplied directories and ingests only files with allowed
extensions (`.pdf`, `.docx`, `.pptx`, `.md`, `.txt`, `.markdown`, `.tex`).
Watch roots are restricted to the operator-configured `SAGE_WATCH_DIRS` bases:
the resolved path must fall within one of them (rejected otherwise, and
filesystem roots are rejected outright), and when no `SAGE_WATCH_DIRS` are
configured `POST /api/watch` rejects with an error asking the operator to
configure them. The runtime API can only enable or disable those configured
bases; it cannot add arbitrary directories. Paths are stored in resolved
canonical form (aliases collapse to one source), re-adding a disabled source
re-enables it without resetting its scan history, and deletes are soft
(tombstoned, preserving `last_scan_at`). Scans are claimed atomically per
source (a conditional update on `last_scan_at`) so concurrent or repeated
calls cannot double-scan, run on worker threads under a shared scan lock, and
are rate-limited; symlinks are never followed (file or directory).

## Prompt injection from study files

Uploaded `.tex`, `.pdf`, and other study files are untrusted content that is
passed to the model endpoint inside `[DOC]`/`[/DOC]` delimited blocks. The
system prompt instructs the model to treat that material as data, not
instructions; delimiting is the only control between source text and prompt
instructions.
