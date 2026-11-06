"""PubTator3 API client: rate limiting, retries, and an on-disk response cache."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

API_BASE = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"

# PubTator3 asks users to post no more than three requests per second.
MAX_REQUESTS_PER_SECOND = 3.0

CACHE_TTL_SECONDS = 30 * 24 * 3600


class PubTatorError(Exception):
    """A request failed after retries, or returned something unusable."""


class QueryError(PubTatorError):
    """PubTator3 rejected the query itself (HTTP 400); retrying will not help."""


def default_cache_path() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "pubmatrix" / "cache.sqlite"


class RateLimiter:
    """Hands out evenly spaced request slots across threads."""

    def __init__(self, per_second: float, sleep: Callable[[float], None] = time.sleep):
        self._interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()
        self._sleep = sleep

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            self._sleep(slot - now)


class Cache:
    """Key/value store of successful API responses, shared across runs."""

    def __init__(self, path: Path, ttl: float = CACHE_TTL_SECONDS):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL)"
        )
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value, fetched_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if row is None or time.time() - row[1] > self._ttl:
            return None
        return json.loads(row[0])

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._db.commit()


class PubTatorClient:
    def __init__(
        self,
        cache: Cache | None = None,
        *,
        refresh: bool = False,
        rate: float = MAX_REQUESTS_PER_SECOND,
        retries: int = 4,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "pubmatrix-reloaded/0.1"},
        )
        self._cache = cache
        self._refresh = refresh
        self._limiter = RateLimiter(rate, sleep)
        self._retries = retries
        self._sleep = sleep

    def count(self, text: str) -> int:
        """Return the number of documents PubTator3 finds for a search query."""
        key = f"count:{text}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        data = self._get("/search/", {"text": text})
        if not isinstance(data, dict) or not isinstance(data.get("count"), int):
            raise PubTatorError(f"search response has no count for {text!r}")
        return self._store(key, data["count"])

    def autocomplete(self, query: str, limit: int = 5) -> list[dict]:
        """Return PubTator3's candidate entities for a term, best first."""
        key = f"autocomplete:{limit}:{query}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        data = self._get("/entity/autocomplete/", {"query": query, "limit": limit})
        if not isinstance(data, list):
            raise PubTatorError(f"autocomplete response is not a list for {query!r}")
        return self._store(key, data)

    def _cached(self, key: str) -> Any | None:
        if self._cache is None or self._refresh:
            return None
        return self._cache.get(key)

    def _store(self, key: str, value: Any) -> Any:
        if self._cache is not None:
            self._cache.put(key, value)
        return value

    def _get(self, path: str, params: dict) -> Any:
        problem = "no attempt made"
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(self._backoff(attempt, problem))
            self._limiter.wait()
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code == 400:
                raise QueryError(f"PubTator3 rejected the query (HTTP 400): {params}")
            if response.status_code == 429 or response.status_code >= 500:
                problem = response
                continue
            if response.status_code != 200:
                raise PubTatorError(f"HTTP {response.status_code} for {params}")
            try:
                return response.json()
            except ValueError:
                problem = "response was not JSON"
        if isinstance(problem, httpx.Response):
            problem = f"HTTP {problem.status_code}"
        raise PubTatorError(f"gave up after {self._retries + 1} attempts: {problem}")

    @staticmethod
    def _backoff(attempt: int, problem: Any) -> float:
        if isinstance(problem, httpx.Response):
            retry_after = problem.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return float(retry_after)
        return float(2**attempt)
