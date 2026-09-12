---
title: Setup
---
# Setup

Sage runs in Docker anywhere.

```mermaid
flowchart TB
    A["Install Docker + Compose"] --> B["cp .env.example .env"]
    B --> C["Set MODEL and point API_URL at an OpenAI endpoint"]
    C --> D["docker compose up -d"]
    D --> E["Sage container (sage:latest)"]

    subgraph LLM["LLM backend"]
        G{"Which endpoint?"}
        G -->|"local Ollama container"| H["http://ollama:11434, on the compose network"]
        G -->|"Ollama cloud"| I["https://ollama.com/v1"]
        G -->|"any OpenAI-compatible"| J["your endpoint base URL"]
    end
    subgraph NET["Networking & access"]
        K["http://localhost:8000. local (PORT override)"]
        L["http://HOST-IP:8000. LAN, open the port in the firewall"]
        M["http://TAILSCALE-IP:8000. remote over Tailscale"]
    end
    subgraph DATA["Persistence"]
        F["./data volume. SQLite · uploads · watch state (/data)"]
    end
    subgraph GROUND["Optional web grounding"]
        N["SEARXNG_URL instance → grounded web results"]
    end

    C --> G
    H --> E
    I --> E
    J --> E
    E --> F
    E --> K
    E --> L
    E --> M
    N --> E

    style LLM fill:#191724,stroke:#c4a7e7
    style NET fill:#191724,stroke:#eb6f92
    style DATA fill:#191724,stroke:#f6c177
    style GROUND fill:#191724,stroke:#f6c177
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