"""PubTator3 API client: rate limiting, retries, and an on-disk response cache."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from . import __version__
from .schedule import QuietHours

API_BASE = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"

# PubTator3 asks users to post no more than three requests per second.
MAX_REQUESTS_PER_SECOND = 3.0

CACHE_TTL_SECONDS = 30 * 24 * 3600
MAX_BACKOFF_SECONDS = 60.0


class PubTatorError(Exception):
    """A request failed after retries, or returned something unusable."""


class QueryError(PubTatorError):
    """PubTator3 rejected the query itself (HTTP 400); retrying will not help."""


class Stopped(PubTatorError):
    """The run was told to stop before this request was made."""


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

    def lookup(self, key: str) -> tuple[Any, float] | None:
        """Return (value, time fetched), or None if absent or expired."""
        with self._lock:
            row = self._db.execute(
                "SELECT value, fetched_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if row is None or time.time() - row[1] > self._ttl:
            return None
        return json.loads(row[0]), row[1]

    def get(self, key: str) -> Any | None:
        hit = self.lookup(key)
        return None if hit is None else hit[0]

    def put(self, key: str, value: Any, fetched_at: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                (key, json.dumps(value), fetched_at),
            )
            self._db.commit()


def _iso(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    return moment.isoformat(timespec="seconds")


class PubTatorClient:
    def __init__(
        self,
        cache: Cache | None = None,
        *,
        refresh: bool = False,
        rate: float = MAX_REQUESTS_PER_SECOND,
        patience: float = 300.0,
        timeout: float = 60.0,
        window: QuietHours | None = None,
        on_pause: Callable[[datetime], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], Any] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """`patience` is how many seconds one request may spend waiting to retry."""
        self._http = httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": f"pubmatrix-reloaded/{__version__}"},
        )
        self._cache = cache
        self._refresh = refresh
        self._patience = patience
        self._window = window
        self._on_pause = on_pause
        self._now = now
        self._stop = threading.Event()
        # Waiting on the stop event means even an hours-long pause ends at once.
        self._sleep = sleep or self._stop.wait
        self._limiter = RateLimiter(rate, self._sleep)
        self._pause_lock = threading.Lock()
        self._announced: datetime | None = None

    def stop(self) -> None:
        """Make waiting and future requests end with Stopped. Cannot be undone."""
        self._stop.set()

    def count(self, text: str) -> int:
        """Return the number of documents PubTator3 finds for a search query."""
        return self.fetch_count(text)[0]

    def fetch_count(self, text: str) -> tuple[int, str]:
        """Return the document count and when PubTator3 gave it (ISO 8601, UTC)."""
        key = f"count:{text}"
        hit = self._cached(key)
        if hit is not None:
            return hit
        data = self._get("/search/", {"text": text})
        if not isinstance(data, dict) or not isinstance(data.get("count"), int):
            raise PubTatorError(f"search response has no count for {text!r}")
        return self._store(key, data["count"])

    def autocomplete(self, query: str, limit: int = 5, concept: str | None = None) -> list[dict]:
        """Return PubTator3's candidate entities for a term, best first.

        `concept` limits candidates to one type, such as "CHEMICAL".
        """
        key = f"autocomplete:{limit}:{concept}:{query}"
        hit = self._cached(key)
        if hit is not None:
            return hit[0]
        params: dict[str, Any] = {"query": query, "limit": limit}
        if concept:
            params["concept"] = concept
        data = self._get("/entity/autocomplete/", params)
        if not isinstance(data, list):
            raise PubTatorError(f"autocomplete response is not a list for {query!r}")
        return self._store(key, data)[0]

    def _cached(self, key: str) -> tuple[Any, str] | None:
        if self._cache is None or self._refresh:
            return None
        hit = self._cache.lookup(key)
        return None if hit is None else (hit[0], _iso(hit[1]))

    def _store(self, key: str, value: Any) -> tuple[Any, str]:
        fetched_at = time.time()
        if self._cache is not None:
            self._cache.put(key, value, fetched_at)
        return value, _iso(fetched_at)

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise Stopped("the run was stopped")

    def _hold_for_quiet_hours(self) -> None:
        while self._window is not None and not self._stop.is_set():
            moment = self._now()
            if self._window.is_open(moment):
                return
            opens = self._window.next_open(moment)
            with self._pause_lock:
                if self._on_pause and opens != self._announced:
                    self._on_pause(opens)
                self._announced = opens
            self._sleep(max(1.0, opens.timestamp() - moment.timestamp()))

    def _get(self, path: str, params: dict) -> Any:
        waited = 0.0
        attempt = 0
        while True:
            self._hold_for_quiet_hours()
            self._check_stop()
            self._limiter.wait()
            self._check_stop()
            attempt += 1
            retry_after = None
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 400:
                    raise QueryError(f"PubTator3 rejected the query (HTTP 400): {params}")
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError:
                        problem = "response was not JSON"
                elif response.status_code == 429 or response.status_code >= 500:
                    problem = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After", "")
                else:
                    raise PubTatorError(f"HTTP {response.status_code} for {params}")
            delay = min(MAX_BACKOFF_SECONDS, float(2**attempt))
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
            if waited + delay > self._patience:
                raise PubTatorError(f"gave up after {attempt} attempts: {problem}")
            waited += delay
            self._sleep(delay)
