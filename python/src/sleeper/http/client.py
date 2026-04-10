from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from sleeper.errors import SleeperApiError, SleeperNotFoundError, SleeperRateLimitError

BASE_URL = "https://api.sleeper.app/v1"

MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]
RATE_LIMIT_TOKENS = 1000
RATE_LIMIT_INTERVAL = 60.0  # seconds


class _RateLimiter:
    """Token-bucket rate limiter: 1000 requests per 60 seconds."""

    def __init__(self, tokens: int = RATE_LIMIT_TOKENS, interval: float = RATE_LIMIT_INTERVAL):
        self._max_tokens = tokens
        self._tokens = float(tokens)
        self._interval = interval
        self._refill_rate = tokens / interval  # tokens per second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
            else:
                wait = (1 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)
                self._tokens = 0
                self._last_refill = time.monotonic()


class HttpClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._rate_limiter = _RateLimiter()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._rate_limiter.acquire()

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await self._client.get(path, params=params)

                if response.status_code == 200:
                    return response.json()
                if response.status_code == 404:
                    raise SleeperNotFoundError(f"GET {path}")
                if response.status_code == 429:
                    raise SleeperRateLimitError()
                if response.status_code >= 500:
                    raise SleeperApiError(response.status_code, f"GET {path}")

                raise SleeperApiError(response.status_code, f"GET {path}: {response.text}")

            except SleeperApiError as e:
                if e.status_code < 500:
                    raise
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
            except httpx.HTTPError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])

        raise last_error  # type: ignore[misc]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
