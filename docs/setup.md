---
title: Setup
---
# Setup

Sage runs in Docker anywhere.

```mermaid
flowchart TB
    subgraph HOST["Your machine"]
        A["Install Docker + Compose"] --> B["cp .env.example .env"]
        B --> C["Set MODEL and point API_URL at an OpenAI endpoint"]
        C --> D["docker compose up -d"]
        D --> E["Sage container (sage:latest)"]
    end
    subgraph DATA["Persistence"]
        E --> F["./data volume — SQLite · uploads · watch state (/data)"]
    end
    subgraph LLM["LLM backend"]
        C --> G{"Which endpoint?"}
        G -->|"local Ollama container"| H["http://ollama:11434 — on the compose network"]
        G -->|"Ollama cloud"| I["https://ollama.com/v1"]
        G -->|"any OpenAI-compatible"| J["your endpoint base URL"]
        H --> E
        I --> E
        J --> E
    end
    subgraph NET["Networking & access"]
        E --> K["http://localhost:8000 — local (PORT override)"]
        E --> L["http://HOST-IP:8000 — LAN, open the port in the firewall"]
        E --> M["http://TAILSCALE-IP:8000 — remote over Tailscale"]
    end
    subgraph GROUND["Optional web grounding"]
        N["SEARXNG_URL instance → grounded web results"] --> E
        O["/api/health surfaces config problems (bad API_KEY / API_URL)"] -.-> P["SSE turns fail fast"]
    end
    style HOST fill:#191724,stroke:#9ccfd8
    style LLM fill:#191724,stroke:#c4a7e7
    style NET fill:#191724,stroke:#eb6f92
    style GROUND fill:#191724,stroke:#f6c177
    style DATA fill:#191724,stroke:#f6c177
```

1. Install Docker and Compose.
2. `cp .env.example .env`, set `MODEL`. See [[environment|Environment variables]].
3. `docker compose up -d`.
4. Open the UI at `http://localhost:${PORT:-8000}`.

Sage needs an OpenAI-compatible LLM. The compose default targets a local Ollama
container; set `API_URL` to Ollama cloud (`https://ollama.com/v1`) or any other
OpenAI-compatible endpoint instead. Read [[security|Security]] before exposing
it on a network.

## See also
- [[index|Sage]]
- [[environment|Environment variables]]