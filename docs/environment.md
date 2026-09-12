---
title: Environment variables
---
# Environment variables

Set in `.env` (copy `.env.example`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_URL` | `http://ollama:11434` | OpenAI-compatible endpoint base (compose default = local Ollama) |
| `API_KEY` | `local` | API key |
| `MODEL` | *(required)* | Model name |
| `SEARXNG_URL` | *(none)* | SearXNG base URL for web-search grounding |
| `SAGE_PASSWORD` | *(none)* | If set, the web app asks for this password before it opens |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port (compose maps `${PORT:-8000}`) |
| `DATA_DIR` | `~/.local/share/sage` | SQLite DB + uploads (container forces `/data`) |
| `MAX_UPLOAD_MB` / `MAX_TOTAL_MB` | `30` / `500` | Upload / library limits |
| `CHUNK_CHARS` / `CHUNK_OVERLAP` | `1500` / `200` | Chunk size / overlap |
| `CONTEXT_CHUNK_BUDGET` | `8` | Max chunks sent per turn |
| `OLLAMA_NUM_CTX` | `16384` | Context window in tokens. lower it to fit a small GPU (<8GB VRAM) |
| `SAGE_LIGHTWEIGHT` | `false` | `true` swaps in a slim small-model prompt + trimmed tool schemas for ≤8B models |
| `SAGE_WATCH_DIRS` | *(none)* | JSON array of note folders to auto-scan |
| `SAGE_WATCH_SCAN_SECONDS` | `300` | Seconds between watch scans |

`API_URL=https://ollama.com` is auto-normalized to `https://ollama.com/v1`. A
missing placeholder `API_KEY` or invalid `API_URL` shows up in `/api/health`.

## See also
- [[index|Sage]]
- [[setup|Setup]]