---
title: Streaming Chat & SSE
description: "SSE event flows for streaming chat: /turns, /retry, /stop, citations, heartbeats."
---
# Streaming Chat & SSE

SSE event flows for streaming chat: /turns, /retry, /stop, citations, heartbeats.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant F as Frontend
    participant A as Backend
    participant M as Model

    Note over U,F: Send a grounded chat turn
    U->>F: type a message
    F->>A: POST /api/sessions/{id}/turns {message, client_msg_id}
    A->>A: retrieve chunks from ready session files (grounding mode)

    alt strict mode + no matching chunks
        A-->>F: SSE: data {type:meta, chunks:[], insufficient:true}
        A-->>F: SSE: data {type:delta, delta:"source-insufficiency notice"}
        A-->>F: SSE: data {type:done, ...}
        Note over F: no model call, no citations emitted
    else grounded mode (or chunks found)
        A->>M: prompt with selected chunks
        A-->>F: SSE: data {type:meta, chunks:[{chunk_id,file_name,location,preview}]}
        loop token stream
            M-->>A: tokens
            A-->>F: SSE: data {type:delta, delta:"text fragment"}
        end
        alt model cites a chunk that was sent in meta
            A-->>F: SSE: data {type:citation, chunk_id}
            F->>F: make citation clickable
            F->>A: GET /api/files/{file_id}/excerpts?chunk={chunk_id}
            A-->>F: {chunk_id, file_name, location, text, context_around}
        end
    end

    A-->>F: SSE: data {type:done, message_id, client_msg_id, replayed:false}
    Note over F: keep stream open — ignore ": ping" heartbeat comments

    Note over U,F: Retry the same turn (same client_msg_id)
    F->>A: POST /api/sessions/{id}/retry {client_msg_id}
    alt message already completed
        A-->>F: SSE: data {type:done, replayed:true} — no model call
    else failed / leftover partial message
        A->>M: regenerate the turn
        A-->>F: SSE: delta* then done
    else unknown client_msg_id
        A-->>F: SSE: data {type:error, code:not_found}
    end
    Note over F: reuse the same bubble — never create a second one

    Note over U,F: Stop generation
    F->>A: POST /api/sessions/{id}/stop {}
    A-->>F: {ok: true}
    A-->>F: SSE: data {type:error, code:cancelled, retryable}
    F->>F: disable Stop, enable Retry if retryable
```

## See also
- [[01-system-architecture]]
- [[04-complete-ui-flow-sequence]]
- [[03-session-state-machine]]
