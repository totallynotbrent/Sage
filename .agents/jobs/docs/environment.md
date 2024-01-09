# Sage — Environment and testing notes

Operational facts for Sage (a local AI tutor): where development happens, where the app runs, how the model APIs are reached, and how secrets and tests work. Recorded so future testing and implementation work uses the correct host topology and secret handling.

## Topology (Windows workspace ↔ Raspberry Pi NAS)

- Development happens on a **Windows workstation** that edits the repository over the NAS share at `\\raspberrypi\brentNAS\Bots\sage`.
- The app **runs on the Raspberry Pi NAS** on the LAN at `http://192.168.1.57/` (Raspberry Pi OS, aarch64/ARM64).
- The Windows machine is the editing surface; the Pi is the runtime host. Files are shared between them over the NAS SMB share.

## Model endpoint reachability (loopback on-Pi vs LAN URL)

- All model APIs — including the local Freebuff OpenAI-compatible endpoint — are hosted **on the Pi**.
- The project's `.env` sets `FREEBUFF_BASE_URL=http://127.0.0.1:8877/v1`. That loopback address only works when the app runs **on the Pi itself**.
- From the Windows workspace or any other LAN device, the reachable URL is `http://192.168.1.57:8877/v1` — the same endpoint, using the Pi's LAN address instead of loopback.

## Secrets and configuration (.env, gitignored; key never committed/echoed)

- Real secrets live in the project's `.env` file, which is gitignored. Never commit it, and never copy or echo the API key into docs, job logs, or messages.
- Settings are read via pydantic-settings from environment variables plus an optional `.env` file in the CWD (see `app/config.py`).
- Config validation is non-fatal: `/api/health` reports problems, and chat routes raise `ConfigError`.

## Running the offline test suite (fake LLM, no key needed)

- Offline unit/integration tests (`tests/`) do **not** need the real key.
- They use an isolated temp `DATA_DIR` and a scripted `FakeLLM` (`tests/fakes/`, `tests/conftest.py`).
- They run on Windows too — no Pi endpoint or network access required.

## Live endpoint tooling (needs real key; run on Pi or point at LAN URL)

- Live/endpoint-touching tooling **does** need the real key and a reachable endpoint:
  - `tools/smoke_chat.py` — minimal chat against the configured endpoint.
  - `tools/check_wheels.py` — downloads requirements to verify ARM64 wheels; best run on the Pi where the target platform lives.
- On the Pi the `.env` loopback URL works as-is; from Windows, point `FREEBUFF_BASE_URL` at `http://192.168.1.57:8877/v1`.

## Notes / current .env values (model override to -flash)

- Current `.env` values (non-secret):
  - `FREEBUFF_BASE_URL=http://127.0.0.1:8877/v1` (loopback; correct only when running on the Pi)
  - `FREEBUFF_MODEL=deepseek/deepseek-v4-flash`
  - `FREEBUFF_API_KEY=<key in .env>` (do not copy the value into docs or logs)
- Note: `.env.example` and the app default say `deepseek/deepseek-v4-pro`, but the actual `.env` overrides `FREEBUFF_MODEL` to `deepseek/deepseek-v4-flash`.
