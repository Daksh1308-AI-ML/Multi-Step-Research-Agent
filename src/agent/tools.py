"""Tavily search with rate-limit detection and exponential backoff."""

import time

import httpx

TAVILY_URL = "https://api.tavily.com/search"


class RateLimitError(RuntimeError):
    pass


def _retry_after(headers) -> float:
    raw = headers.get("retry-after", "2")
    try:
        return min(float(raw), 30.0)
    except ValueError:
        return 2.0


def tavily_search(
    query: str,
    api_key: str,
    depth: str = "basic",
    max_results: int = 5,
    max_retries: int = 3,
) -> list[dict]:
    """Search Tavily. Retries on 429 (respecting Retry-After) and 5xx with backoff.

    Raises RateLimitError when retries are exhausted on 429.
    """
    payload = {"query": query, "search_depth": depth, "max_results": max_results}
    for attempt in range(max_retries):
        try:
            r = httpx.post(
                TAVILY_URL,
                json=payload,
                timeout=30,
                headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"tavily unreachable: {e}") from e

        if r.status_code == 429:
            time.sleep(_retry_after(r.headers))
            continue
        if r.status_code >= 500 and attempt < max_retries - 1:
            time.sleep(2**attempt)  # exponential backoff
            continue
        r.raise_for_status()
        return r.json().get("results", [])

    raise RateLimitError(f"tavily: rate limited after {max_retries} attempts (429)")
