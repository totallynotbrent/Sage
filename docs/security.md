# Security warning

**Trusted-network-only service.** Sage ships with no authentication. Anyone
who can reach the Pi's IP on your local network can call the API and use the
configured model endpoint. Only run it on a network you trust.

The API key stays server-side and is never delivered to the browser, and usage
is bounded by the single local endpoint. A shared access token is a planned
follow-up.
