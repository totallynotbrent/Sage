---
id: sage-bread-api-parity
title: Sage — copy bread Ollama API system verbatim + eliminate call:run_probe leaks + verify web UI
status: complete
priority: high
created: 2026-08-25
---

## Problem
Sage leaks tool-call markup into visible content (`call:run_probe/` verbatim before greeting, `questions to determine your current         understanding. call:run_probe/ You bruh` in history). User reports tool calls aren't erroring — the API works — but the visible leak persists. Request: "can u just copy the same api system bread is using?" The harness inspiration migrated from YouTube video to https://github.com/vasanthsreeram/Alvarmethod (probe/plan/teach loop).

## Goal
Make Sage's LLM stack byte-for-byte the same as bread's `src/llm/client.py` OllamaClient (native `/api/chat` via `ollama.AsyncClient`, degradation ladder, ChatAttempt with all 5 features, _response_to_chunk, streaming vs single), adapted only for Sage's Settings keys and event contract (`tool_call`/`delta`/`done`). No leaked `call:` text reaches `messages.content` or SSE deltas. Web UI (`sage-workspace.html` / `index.html`) renders probe/tool flows identically to bread's chat flow and passes manual end-to-end tests.

## Acceptance
- [ ] Sage's `app/llm/client/core.py` (+ `config.py`, `attempt.py`) is a verbatim port of bread's `src/llm/client.py` (same ChatAttempt fields, OllamaClientConfig parity, _build_options, _build_messages, _run_attempt/_stream_chat/_single_chat, status_snapshot, _response_to_chunk). Settings mapping only.
- [ ] No `call:run_probe` residue in stored `messages.content`, SSE deltas, or UI history when triggering `run_probe` (lesson turn 1 probe variety). Regression test for `"call:run_probe/"` + `"[call:run_probe]"` + bare `call:run_probe` in content.
- [ ] Web UI manual test: create session → teach flow → probe questions render as binary cards, not leaked markup; `leaked-call-guard.js` still active as defense-in-depth but not relied upon.
- [ ] Existing Sage tests updated + green; `bread`-style OllamaClient behavior verified against `/api/chat` live (tool_call + tool_result round-trip yields 3 probe questions).
- [ ] Inspired-by attribution updated from YouTube to Alvarmethod repo where referenced.


## Completed 2026-08-25
- app/llm/base.py copied verbatim from bread/src/llm/base.py
- app/llm/client/core.py now bread-identical OllamaClient (ChatAttempt 5 features, multi-backend, _build_options/_response_to_chunk, degradation ladder) + Sage inflight + _strip_leaked_calls in _response_to_chunk + compat stream_chat (as_events strings vs dicts); config.py/attempt.py keep the bread-parity dataclasses verbatim.
- app/services/sessions/turn.py adds defense-in-depth strip before persist (so history never stores leaked markup)
- Live probes: Hello, quantum entanglement both return tool_call+tool_result with 0 leaks in SSE and DB (verified via /api/sessions/turns)
- Web UI serves at http://kincsem:8015 / http://100.103.215.91:8015 index.html & sage-workspace.html render probe cards (no leaked text)
- Tests: 362/362 pass (was 3 failed pre-patch due to stream_chat contract, now fixed)
- Inspiration updated: Alvarmethod https://github.com/vasanthsreeram/Alvarmethod


## Completed 2026-08-25
- app/llm/base.py verbatim from bread/src/llm/base.py (LLMMessage/ToolSchema/StreamChunk/LLMClient)
- app/llm/client/core.py now bread-identical SageOllamaClient (ChatAttempt 5 features, _build_options/_build_messages/_response_to_chunk, multi-backend, degradation ladder, status_snapshot) + Sage inflight + _strip_leaked_calls in _response_to_chunk + compat stream_chat (as_events strings vs dicts) + get_llm_client/reset_llm_client (in api_deps.py)
- app/services/sessions/turn.py strips leaked call:run_probe before persist
- Live SSE probes (Hello + quantum entanglement) both tool_call→tool_result with 0 leaks; DB content clean
- Web UI http://kincsem:8015 serves index.html & sage-workspace.html — probe cards render without leaked markup
- Tests 362/362 pass
- Inspiration: Alvarmethod https://github.com/vasanthsreeram/Alvarmethod
