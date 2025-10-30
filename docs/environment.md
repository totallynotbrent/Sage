---
title: Environment variables
---
# Environment variables

All variables are read from `.env` (see `.env.example`) by
`app/config.py` (pydantic-settings). Env var names stay `API_URL / API_KEY / MODEL` all-caps.

| Variable              | Default                          | Purpose                             |
| --------------------- | -------------------------------- | ----------------------------------- |
| `API_URL`       | `https://ollama.com/v1`          | Ollama cloud OpenAI-compatible endpoint base URL (https://ollama.com/v1) |
| `API_KEY`        | *(required)*                     | Secret key, server-side only (Ollama cloud key from https://ollama.com/settings/keys) |
| `MODEL`          | `gemma4:31b-cloud`               | Model name (Ollama cloud default)   |
| `SEARXNG_URL`         | *(none)*                         | SearXNG instance base URL, e.g. `http://192.168.1.57:8080` |
| `HOST`                | `0.0.0.0`                        | Bind address                        |
| `PORT`                | `8000`                           | Bind port                           |
| `DATA_DIR`            | `~/.local/share/sage`            | SQLite DB + uploads location        |
| `MAX_UPLOAD_MB`       | `30`                             | Per-file upload limit               |
| `MAX_TOTAL_MB`        | `500`                            | Total library storage limit         |
| `CHUNK_CHARS`         | `1500`                           | Chunk size (chars)                  |
| `CHUNK_OVERLAP`       | `200`                            | Chunk overlap (chars)               |
| `CONTEXT_CHUNK_BUDGET`| `8`                              | Max chunks sent to the model per turn |
| `SAGE_WATCH_DIRS`     | *(none)*                         | JSON array of note folders to auto-scan, e.g. `["/home/brent/notes/calculus"]` |
| `SAGE_WATCH_SCAN_SECONDS` | `300`                         | Seconds between automatic watch scans (first scan sleeps one interval) |

API_URL=https://ollama.com is auto-normalized to https://ollama.com/v1; get a cloud key at https://ollama.com/settings/keys. When SEARXNG_URL is set, grounded turns and teach/latex outputs include web results as [WEB] blocks; strict mode remains file-only; search failures fall back to file-only.

`API_KEY` missing, placeholder, or `API_URL` invalid are surfaced
as configuration problems: they appear in `GET /api/health` and cause SSE
turns to fail fast with a config error event instead of reaching the model.
