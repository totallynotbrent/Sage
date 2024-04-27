from __future__ import annotations

import httpx


async def search_web(base_url: str, query: str, max_results: int = 5) -> list[dict]:
    if not base_url or not query.strip():
        return []
    url = f"{base_url.rstrip('/')}/search"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                url, params={"q": query, "format": "json", "language": "en"}
            )
            if response.status_code == 404:
                response = await client.get(url, params={"q": query})
            if response.status_code != 200:
                return []
            data = response.json()
            if isinstance(data, list):
                raw = data
            elif isinstance(data, dict):
                raw = data.get("results")
                if raw is None:
                    raw = data.get("data") or []
                if not isinstance(raw, list):
                    raw = []
            else:
                raw = []
            out: list[dict] = []
            for item in raw[:max_results]:
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or "Untitled"
                url_val = item.get("url") or item.get("link") or ""
                content = (
                    item.get("content")
                    or item.get("snippet")
                    or item.get("summary")
                    or ""
                )
                out.append(
                    {
                        "title": str(title),
                        "url": str(url_val),
                        "content": str(content),
                        "snippet": str(content),
                    }
                )
            return out
    except Exception:
        return []
    return []
