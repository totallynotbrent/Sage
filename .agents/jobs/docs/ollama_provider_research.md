# Ollama Cloud provider research (T30)

Recorded by Zenyatta after two consecutive empty Hermes runs (session exited done,
files_touched=no, no log written). Sources verified via web search on 2026-08-21.

## Endpoint facts

- Base URL (OpenAI-compatible): `https://ollama.com/v1`
  - `/v1/chat/completions` supports: chat, streaming, JSON mode, tools, vision,
    reasoning/thinking control. No logprobs.
  - Native (non-OpenAI) API lives at `https://ollama.com/api` — not used by Sage.
  - Source: https://docs.ollama.com/api/openai-compatibility ;
    https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx
- Auth: `Authorization: Bearer $OLLAMA_API_KEY`; keys created at
  https://ollama.com/settings/keys ; keys do not expire (revocable).
  - Source: https://docs.ollama.com/api/authentication
- AsyncOpenAI usage: `base_url="https://ollama.com/v1"`, `api_key=<key>`
  (api_key required by SDK, honored as Bearer).

## Model: gemma4:31b-cloud

- Exists: https://ollama.com/library/gemma4:31b-cloud (tag c382fbfbc73b).
- 31B dense (30.7B params), 60 layers, **256K context**, vocab 262K.
- Modalities: text + image input, text output. Native function calling.
- Benchmarks (Ollama library page): MMLU Pro 85.2, GPQA Diamond 84.3,
  AIME 2026 89.2 — strong fit for a study tutor.
- Ollama retired gemma3:12b/27b in favor of gemma4:31b (docs.ollama.com/cloud),
  so this is the current-generation Gemma line.

## Behavior quirks that affect Sage

- **Thinking mode**: enabled by including `<|think|>` at the start of the system
  prompt. When enabled the model emits internal reasoning inside
  `<|channel>thought ... channel|>` before the final answer; when disabled most
  variants still emit an empty thought block.
  - Consequence for Sage: streamed deltas may include thought-channel content.
    The SSE chat path must exclude thought content from lesson text and from
    persisted messages, or reasoning leaks into the tutoring transcript.
  - Multi-turn rule: history must contain only final answers, never thoughts
    (official best-practice note on the library page).
- Sampling guidance from Google/Ollama: temperature=1.0, top_p=0.95, top_k=64
  (we will keep our own conservative defaults; noted for reference).
- Structured outputs: JSON mode is supported via /v1; provider-side strict
  json_schema enforcement is NOT listed for gemma4 — Sage's existing
  prompt-schema + local Pydantic validation path remains mandatory (already built).

## Recommendations for implementation

1. Point the LLM client at `https://ollama.com/v1` with Bearer key; rename
   config/env to provider-neutral `ollama_*` names pending user confirmation.
2. Keep the existing complete_json + repair pipeline; do not assume provider
   schema enforcement.
3. Filter/strip `<|channel>thought ... channel|>` blocks and any
   `reasoning`/`thinking` delta fields in stream_chat; persist final answers only.
4. Do not put `<|think|>` in system prompts unless a "show reasoning" toggle is
   wanted later; default off keeps transcripts clean and tokens cheaper.
5. Timeouts: cloud 31B generation is slower than the old local endpoint; raise
   request timeout defaults modestly (e.g., 120s connect/read) rather than the
   previous local-model assumptions.

## Confidence

- Endpoint URL, auth scheme, feature matrix: verified (official docs + repos).
- gemma4:31b-cloud existence/specs/thinking behavior: verified (official library page).
- Exact delta field name for thought content under /v1 streaming: inferred
  (documented as "Reasoning/thinking control" supported; exact wire field to be
  confirmed during live testing with the real key).
