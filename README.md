# Sage — Local Learning Agent

A local-first web application that acts as a personalized AI tutor. Attach
study files (PDF, DOCX, PPTX, Markdown, text), and interact with a grounded,
streaming chat tutor that cites the exact excerpts it used.

- **Backend:** Python 3.11, FastAPI, uvicorn — a single process serves the API
  and the static frontend (same origin, no proxy, no build step).
- **Frontend:** vanilla HTML/CSS/JS, no CDN at runtime, no bundler.
- **Model:** any OpenAI-compatible endpoint (default: local Freebuff at
  `http://127.0.0.1:8877/v1`).
- **Storage:** SQLite + files in `DATA_DIR` (default `~/.local/share/sage`),
  uploads kept outside the static tree.

## Setup

Requirements: Python 3.11 on a 64-bit ARM64 Raspberry Pi (Raspberry Pi OS) or
any Linux/Windows machine.

Raspberry Pi OS note: Bookworm (Debian 12) ships `python3.11` via apt. The
current Raspberry Pi OS Trixie (Debian 13) does **not** — install Python 3.11
with `uv python install 3.11` (or pyenv) there.

```bash
# Copy the example config and fill in your key
cp .env.example .env

# One-command start (Linux/Pi)
./run.sh

# Windows development equivalent
run.bat
```

`run.sh` creates `.venv` when absent (preferring `python3.11`, falling back to
`python3`), installs `requirements.txt`, then serves:

```
uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}
```

## LAN access

The app binds to `0.0.0.0:8000` by default, so any device on your local
network can reach it at:

```
http://<pi-ip>:8000
```

Find the Pi's address with `hostname -I`, its mDNS hostname, or your router's
DHCP client list. `HOST`/`PORT` environment variables override the bind address
and port; the defaults remain LAN-reachable.

## Security warning

**Trusted-network-only service.** Sage ships with no authentication. Anyone
who can reach the Pi's IP on your local network can open the UI and use the
configured model endpoint. Only run it on a network you trust. The API key
stays server-side and is never delivered to the browser, and usage is bounded
by the single local endpoint. A shared access token is a planned follow-up.

## Environment variables

| Variable           | Default                          | Purpose                             |
| ------------------ | -------------------------------- | ----------------------------------- |
| `FREEBUFF_BASE_URL`| `http://127.0.0.1:8877/v1`       | OpenAI-compatible endpoint base URL |
| `FREEBUFF_API_KEY` | *(required)*                     | Secret key, server-side only        |
| `FREEBUFF_MODEL`   | `deepseek/deepseek-v4-pro`       | Model name                          |
| `HOST`             | `0.0.0.0`                        | Bind address                        |
| `PORT`             | `8000`                           | Bind port                           |
| `DATA_DIR`         | `~/.local/share/sage`            | SQLite DB + uploads location        |
| `MAX_UPLOAD_MB`    | `30`                             | Per-file upload limit               |
| `MAX_TOTAL_MB`     | `500`                            | Total library storage limit         |
| `CHUNK_CHARS`      | `1500`                           | Chunk size (chars)                  |
| `CHUNK_OVERLAP`    | `200`                            | Chunk overlap (chars)               |
| `CONTEXT_CHUNK_BUDGET` | `8`                          | Max chunks sent to the model per turn |

## Tests

```bash
python -m pytest tests/ -q
```

All tests run offline (no live model required); the LLM is faked. The
extraction tests generate tiny PDF/DOCX/PPTX fixtures on the fly and skip
gracefully if a library is unavailable.

## Preflight wheel check

```bash
python tools/check_wheels.py
```

Downloads every requirement without dependencies and — on Linux — verifies an
ARM64/aarch64 or `any` wheel exists for each package. A package without an
ARM64 Python 3.11 wheel fails fast here with an actionable message rather than
as a mid-session runtime traceback.

## Live endpoint smoke test

```bash
python tools/smoke_chat.py
```

Sends a minimal request to the configured endpoint (requires a running model
and a valid key). Not part of the test suite.

## Roadmap

The acceptance checklist from spec §15 is added here once the remaining phases
(probe, plan, teach/check loops, mastery, data management) land.
