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
