---
title: Learning Loop
description: "The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete."
---
# Learning Loop

The end-to-end tutoring loop: files to chunks to session to probe, plan, teach, check, remediate, complete.

```mermaid
flowchart LR
    A["1. Study files<br/>.pdf .docx .pptx .md .markdown .txt"] --> B["2. Extract + chunk<br/>CHUNK_CHARS=1500, CHUNK_OVERLAP=200"]
    B --> C["3. Searchable chunks<br/>file status: ready / partial / failed"]
    C --> D["4. Session<br/>goal + selected files + grounding mode"]
    D --> E{"5. Diagnostic probe?<br/>(optional)"}
    E -->|"no — go straight to plan"| H
    E -->|"yes"| F["Probe — 3 adaptive questions<br/>server appends 'I don't know' option"]
    F --> G["Mastery evidence<br/>recorded per answer"]
    G --> H["6. Learning plan<br/>dependency-aware plan nodes"]
    H --> I["7. Teach each node<br/>streaming grounded chat + citations"]
    I --> J{"8. Periodic check<br/>check_due after 2 nodes"}
    J -->|"correct"| I
    J -->|"incorrect / idk"| K["9. Remediation<br/>hint / reveal / chat → Continue"]
    K -->|"POST /continue"| I
    J -->|"skip question"| H
    I -->|"no pending nodes"| L["10. Complete<br/>summary + read-only history"]
    D --> M["Grounded chat tutor<br/>retrieves from session files<br/>strict mode = sources required"]
    C --> M
    G --> I
    G --> J
```

## See also
- [[01-system-architecture]]
- [[03-session-state-machine]]
- [[08-latex-notes]]
