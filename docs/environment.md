# Environment variables

All variables are read from `.env` (see `.env.example`) by
`app/config.py` (pydantic-settings). Env var names stay `BROT_*` all-caps.

| Variable              | Default                          | Purpose                             |
| --------------------- | -------------------------------- | ----------------------------------- |
| `BROT_BASE_URL`       | `http://127.0.0.1:8877/v1`       | OpenAI-compatible endpoint base URL |
| `BROT_API_KEY`        | *(required)*                     | Secret key, server-side only        |
| `BROT_MODEL`          | `deepseek/deepseek-v4-pro`       | Model name                          |
| `HOST`                | `0.0.0.0`                        | Bind address                        |
| `PORT`                | `8000`                           | Bind port                           |
| `DATA_DIR`            | `~/.local/share/sage`            | SQLite DB + uploads location        |
| `MAX_UPLOAD_MB`       | `30`                             | Per-file upload limit               |
| `MAX_TOTAL_MB`        | `500`                            | Total library storage limit         |
| `CHUNK_CHARS`         | `1500`                           | Chunk size (chars)                  |
| `CHUNK_OVERLAP`       | `200`                            | Chunk overlap (chars)               |
| `CONTEXT_CHUNK_BUDGET`| `8`                              | Max chunks sent to the model per turn |

`BROT_API_KEY` missing, placeholder, or `BROT_BASE_URL` invalid are surfaced
as configuration problems: they appear in `GET /api/health` and cause SSE
turns to fail fast with a config error event instead of reaching the model.
