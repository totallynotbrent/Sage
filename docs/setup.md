# Setup

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

## Notes folder watch

Point Sage at LaTeX notes folders with `SAGE_WATCH_DIRS` — a JSON array of
absolute paths, e.g. `SAGE_WATCH_DIRS=["/home/brent/notes/calculus"]` — and
`SAGE_WATCH_SCAN_SECONDS` for the interval (default 300). The watcher starts
with the app (lifespan) and, on every interval, ingests new or changed
`.tex`/`.md`/PDF files into the library, re-ingests changed files, and tags
each file's `subject` from its first folder segment. A same-stem `.tex`/`.pdf`
pair is linked automatically. Manage sources live: `GET/POST /api/watch`
lists/registers a folder, `POST /api/watch/scan` scans now, and
`DELETE /api/watch/{id}` stops watching one. The first scan sleeps one full
interval so it never races an explicit `POST /api/watch/scan`.

## LAN access

The app binds to `0.0.0.0:8000` by default, so any device on your local
network can reach it at:

```
http://<pi-ip>:8000
```

Find the Pi's address with `hostname -I`, its mDNS hostname, or your router's
DHCP client list. `HOST`/`PORT` environment variables override the bind address
and port; the defaults remain LAN-reachable.

See [environment.md](environment.md) for the full environment variable
reference, and [security.md](security.md) before exposing the service on a
network.
