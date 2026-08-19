```
   _____         _____ ______ 
  / ____|  /\   / ____|  ____|
 | (___   /  \ | |  __| |__   
  \___ \ / /\ \| | |_ |  __|  
  ____) / ____ \ |__| | |____ 
 |_____/_/    \_\_____|______|
```

## Summary

Sage is a local-first web application that acts as a personalized AI tutor: attach study files (PDF, DOCX, PPTX, Markdown, text), and Sage chunks them, probes what you already know, builds a learning plan, teaches each node, checks understanding with quiz questions, and remediates weak spots — all through a grounded, streaming chat that cites the exact excerpts it used. It ships as an API-only Python 3.11 FastAPI service (JSON/SSE endpoints; no web UI for now) that talks to any OpenAI-compatible model endpoint (default: the local BROT server at `http://127.0.0.1:8877/v1`), and stores everything in SQLite plus files under `DATA_DIR` (default `~/.local/share/sage`), with uploads kept outside any served path.

## Project structure

```
sage/
├── app/
│   ├── main.py                      # FastAPI app factory · init_db · 8 routers
│   ├── config.py                    # Settings — BROT_* / HOST / PORT / limits, read from .env
│   ├── db.py                        # SQLite schema v1 · connection helpers
│   ├── models.py                    # Pydantic request/response models
│   ├── errors.py                    # error envelope + exception handlers
│   ├── sse.py                       # SSE helper — 15s heartbeat · sse_response
│   ├── logging_setup.py             # logging configuration
│   ├── util.py                      # id / utc_now helpers
│   ├── api/                         # HTTP routers
│   │   ├── deps.py                  #   require_configured · handle_value_error
│   │   ├── system.py                #   GET /api/health
│   │   ├── files.py                 #   /api/files upload · list · excerpts · retry · delete
│   │   ├── sessions.py              #   /api/sessions CRUD · select files · grounding
│   │   ├── chat.py                  #   /turns · /retry (SSE) · /stop
│   │   ├── learning.py              #   /probe · /check · quiz answer
│   │   ├── plans.py                 #   /plan generate · approve · reorder · skip · expand · regenerate · select
│   │   ├── teach.py                 #   /advance · /continue · /complete · quiz hint/reveal/skip
│   │   └── preferences.py           #   /api/preferences · /api/mastery reset
│   ├── llm/
│   │   ├── client.py                # AsyncOpenAI client → BROT · stream_chat · cancel_inflight
│   │   ├── messages.py              # chat message builder · citation markers
│   │   └── structured.py            # structured probe/plan question requests
│   └── services/
│       ├── sessions/                # session state machine package
│       │   ├── __init__.py          #   SessionService export
│       │   ├── session.py           #   PHASES · CRUD · select_files · grounding
│       │   ├── turn.py              #   SSE turn/retry stream generators
│       │   └── rows.py              #   plan node / quiz question row mappers
│       ├── files.py                 # upload · storage · chunk persistence
│       ├── chunking.py              # CHUNK_CHARS / CHUNK_OVERLAP splitter
│       ├── retrieval.py             # context-budget chunk selection
│       ├── learning.py              # probe/check generation · quiz grading
│       ├── plans.py                 # plan generate/approve/reorder/skip/expand
│       ├── teach.py                 # advance/continue/complete · hint/reveal
│       ├── mastery.py               # mastery_topics confidence tracking
│       └── extraction/              # PDF · DOCX · PPTX · Markdown · text → plain text
├── tools/
│   ├── check_wheels.py              # ARM64 wheel preflight check
│   └── smoke_chat.py                # live endpoint smoke test
├── tests/                           # offline pytest suite (faked LLM)
│   ├── conftest.py
│   ├── fakes/fake_llm.py
│   └── test_*.py                    # API, chunking, retrieval, plans, teach, mastery…
├── docs/                            # operational docs + mermaid diagram sources
│   ├── README.md                    #   index
│   ├── setup.md                     #   install · run · LAN access
│   ├── security.md                  #   trusted-network-only warning
│   ├── environment.md               #   env var reference
│   ├── testing.md                   #   tests · wheel preflight · smoke test
│   └── *.mmd                        #   UI notes diagrams (01-system-architecture … 07-ui-screens)
├── run.sh                           # one-command start (Linux/Pi)
├── run.bat                          # one-command start (Windows)
├── requirements.txt
└── .env.example                     # copy to .env, fill in BROT_API_KEY
```

## Architecture

### System architecture

```mermaid
flowchart LR
    CLIENT["Browser / API client"] -->|"JSON + SSE"| API
    B["BROT — local OpenAI-compatible endpoint<br/>http://127.0.0.1:8877/v1 · POST /chat/completions<br/>model deepseek/deepseek-v4-pro"]

    subgraph APP["Sage — FastAPI service · uvicorn app.main:app"]
        MAIN["app/main.py<br/>create_app · init_db · 8 routers"]
        API["app/api/<br/>system · files · sessions · chat<br/>learning · plans · teach · preferences"]
        DEPS["app/api/deps.py<br/>require_configured<br/>BROT_API_KEY / BROT_BASE_URL validation"]
        SVC["app/services/<br/>sessions · files · chunking · retrieval<br/>learning · plans · teach · mastery · extraction"]
        DB[("SQLite<br/>DATA_DIR/sage.db · 10 tables")]
        UP["uploads on disk<br/>DATA_DIR/uploads · outside served paths"]
        LLM["app/llm/client.py<br/>AsyncOpenAI · stream_chat · quick_probe<br/>cancel_inflight · inflight registry"]
    end

    API --> MAIN
    API --> DEPS
    API --> SVC
    SVC --> DB
    SVC --> UP
    SVC --> LLM
    LLM -->|"httpx · OpenAI SDK"| B
```

### Session state machine

Phases live in `app/services/sessions/session.py` (`PHASES = ("setup", "probe", "plan", "teach", "check", "remediate", "complete")`); the endpoints that drive each transition come from `app/api/`.

```mermaid
stateDiagram-v2
    [*] --> setup: POST /api/sessions (create)
    setup --> probe: POST /api/sessions/{id}/probe
    probe --> plan: POST /api/sessions/{id}/plan
    plan --> teach: POST /api/sessions/{id}/plan/approve
    teach --> check: POST /api/sessions/{id}/check
    check --> remediate: POST /api/sessions/{id}/quiz/{qid}/answer (incorrect / idk)
    remediate --> teach: POST /api/sessions/{id}/continue
    check --> teach: POST /api/sessions/{id}/quiz/{qid}/answer (correct)
    check --> plan: POST /api/sessions/{id}/quiz/{qid}/skip
    teach --> complete: POST /api/sessions/{id}/advance (no pending nodes) · /complete
    complete --> [*]
```

### SSE streaming chat

`POST /api/sessions/{id}/turns` streams `meta` → `delta`* → `citation`* → `done` over one SSE response; `POST /api/sessions/{id}/stop` cancels an inflight generation; `POST /api/sessions/{id}/retry` replays the last user message under the same `client_msg_id`. A `: ping` heartbeat fires whenever the stream is idle for 15s (`app/sse.py`).

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant CH as app/api/chat.py
    participant T as SessionService (turn.py)
    participant DB as SQLite
    participant LLM as LLMClient
    participant B as BROT endpoint

    C->>CH: POST /api/sessions/{id}/turns {message, client_msg_id}
    CH->>T: turn(session_id, message, llm)
    T->>DB: save_partial_marker (partial=1)
    T->>T: _select_chunks (context budget, per_file_cap)
    T-->>C: SSE meta {chunks, insufficient}
    T->>LLM: stream_chat(build_chat_messages)
    LLM->>B: chat.completions.create (stream)
    loop token deltas
        B-->>LLM: delta
        LLM-->>T: delta
        T-->>C: SSE delta {delta}
    end
    T->>DB: persist_message (partial=0, citations_json)
    T-->>C: SSE citation {chunk_id} (per cited chunk)
    T-->>C: SSE done {message_id, client_msg_id, replayed}
    Note over C,T: idle > 15s → heartbeat ": ping"

    alt client stops generation
        C->>CH: POST /api/sessions/{id}/stop
        CH->>LLM: cancel_inflight(session_id)
        LLM-->>T: GenerationCancelled
        T-->>C: SSE error {code: generation_cancelled}
    else provider / upstream failure
        B-->>LLM: ProviderError
        T-->>C: SSE error {code, message, detail, retryable}
    end

    Note over C,CH: POST /api/sessions/{id}/retry — replays last user message with the same client_msg_id
```

### SQLite data model

Schema in `app/db.py` (SCHEMA_VERSION 1). Sessions reference files by JSON array in `file_ids_json` — there is no `session_files` join table. `mastery_topics` and `preferences` are global (not per-session).

```mermaid
erDiagram
    sessions }o--o{ files : "file_ids_json (no FK)"
    files ||--o{ chunks : "has chunks"
    sessions ||--o{ messages : "has messages"
    sessions ||--o{ plan_nodes : "has plan nodes"
    sessions ||--o{ quiz_questions : "has quiz questions"
    sessions ||--o{ feedback_actions : "has feedback actions"

    files {
        text id PK
        text display_name
        text storage_name
        text status "pending / ready / error"
        integer num_chunks
        text sha256 UK
    }
    chunks {
        text id PK
        text file_id FK
        integer chunk_index
        text text
        text location_kind
        integer page
        integer slide
        text section
    }
    sessions {
        text id PK
        text goal
        text phase "setup / probe / plan / teach / check / remediate / complete"
        text grounding_mode "grounded / strict"
        text file_ids_json "JSON array of file ids"
        integer nodes_since_check
    }
    messages {
        text id PK
        text session_id FK
        text client_msg_id
        text role "user / assistant"
        text kind
        text content
        text citations_json "JSON array of chunk ids"
        integer partial
    }
    plan_nodes {
        text id PK
        text session_id FK
        text node_key
        text title
        text status "pending / current / done / skipped"
        integer position
        text depends_on_json
        text children_json
    }
    quiz_questions {
        text id PK
        text session_id FK
        text kind "probe / check"
        text question
        text options_json
        integer correct_index
        text status "pending / answered / skipped"
        text outcome "correct / incorrect / idk"
    }
    feedback_actions {
        text id PK
        text session_id FK
        text question_id
        text action "hint / reveal"
    }
    mastery_topics {
        text topic PK
        text label
        real confidence
        integer observed_count
        integer correct_count
    }
    preferences {
        integer id PK "always 1"
        text depth
        text pacing
        text style
    }
```
