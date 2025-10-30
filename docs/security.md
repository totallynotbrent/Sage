---
title: Security
---
# Security

**Trusted network only.** Sage has no authentication. Anyone who can reach the
port can use it, so only run it on a network you trust. See
[[setup|Setup]] for access.

- The API key stays server-side, never sent to the browser.
- SearXNG, when configured, is queried server-side only.
- The watch API reads only folders inside the configured `SAGE_WATCH_DIRS` bases.
- Study files are untrusted data passed to the model inside `[DOC]` blocks — treated as data, not instructions.

## See also
- [[index|Sage]]