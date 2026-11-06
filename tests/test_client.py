"""Tests for the API client: retries, caching, pacing, and quiet hours."""

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import FakeApi

from pubmatrix.client import (
    Cache,
    PubTatorClient,
    PubTatorError,
    QueryError,
    RateLimiter,
    Stopped,
)
from pubmatrix.schedule import QuietHours


def test_retries_rate_limit_and_server_errors(make_client):
    api = FakeApi({"x": 7}, {"x": [429, 503]})
    assert make_client(api).count("x") == 7
    assert len(api.searches()) == 3


def test_gives_up_after_retries(make_client):
    api = FakeApi(failures={"x": [500] * 10})
    with pytest.raises(PubTatorError, match="HTTP 500"):
        make_client(api, patience=6).count("x")
    assert len(api.searches()) == 3


def test_bad_query_is_not_retried(make_client):
    api = FakeApi(failures={"x": [400]})
    with pytest.raises(QueryError):
        make_client(api).count("x")
    assert len(api.searches()) == 1


def test_retry_after_header_is_honored():
    slept = []
    replies = iter(
        [httpx.Response(429, headers={"Retry-After": "11"}), httpx.Response(200, json={"count": 1})]
    )
    client = PubTatorClient(
        transport=httpx.MockTransport(lambda _: next(replies)),
        sleep=slept.append,
        rate=100000,
    )
    assert client.count("x") == 1
    assert 11.0 in slept


def test_response_without_count_is_an_error():
    client = PubTatorClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []})),
        rate=100000,
    )
    with pytest.raises(PubTatorError):
        client.count("x")


def test_cache_answers_repeat_queries(make_client, tmp_path):
    api = FakeApi({"x": 7})
    cache = Cache(tmp_path / "c.sqlite")
    assert make_client(api, cache=cache).count("x") == 7
    assert make_client(api, cache=cache).count("x") == 7
    assert len(api.searches()) == 1


def test_cache_keeps_true_zero(make_client, tmp_path):
    api = FakeApi()
    cache = Cache(tmp_path / "c.sqlite")
    assert make_client(api, cache=cache).count("x") == 0
    assert make_client(api, cache=cache).count("x") == 0
    assert len(api.searches()) == 1


def test_refresh_bypasses_but_updates_cache(make_client, tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    make_client(FakeApi({"x": 1}), cache=cache).count("x")
    assert make_client(FakeApi({"x": 2}), cache=cache, refresh=True).count("x") == 2
    assert cache.get("count:x") == 2


def test_stale_cache_entries_expire(tmp_path):
    cache = Cache(tmp_path / "c.sqlite", ttl=-1)
    cache.put("k", 1, time.time())
    assert cache.get("k") is None


def test_failures_are_not_cached(make_client, tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    with pytest.raises(QueryError):
        make_client(FakeApi(failures={"x": [400]}), cache=cache).count("x")
    assert cache.get("count:x") is None


def test_rate_limiter_spaces_requests():
    limiter = RateLimiter(50)
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 3 / 50 * 0.9


def test_fetch_count_reports_when_the_answer_was_given(make_client, tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    client = make_client(FakeApi({"x": 7}), cache=cache)
    count, retrieved = client.fetch_count("x")
    assert count == 7
    assert make_client(FakeApi(), cache=cache).fetch_count("x") == (7, retrieved)


class Clock:
    """A clock that only moves when something sleeps."""

    def __init__(self, start):
        self.time = start
        self.naps = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.time += timedelta(seconds=seconds)


# Friday 2026-11-06 at 13:00 Eastern, which is closed.
FRIDAY_AFTERNOON = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)


def test_requests_wait_for_quiet_hours(make_client):
    clock = Clock(FRIDAY_AFTERNOON)
    announced = []
    api = FakeApi({"x": 7})
    client = PubTatorClient(
        transport=httpx.MockTransport(api),
        rate=100000,
        window=QuietHours(),
        on_pause=announced.append,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert client.count("x") == 7
    assert sum(clock.naps) == 8 * 3600
    assert len(announced) == 1


def test_cached_answers_do_not_wait_for_quiet_hours(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.put("count:x", 7, time.time())
    clock = Clock(FRIDAY_AFTERNOON)
    client = PubTatorClient(cache, window=QuietHours(), sleep=clock.sleep, now=clock.now)
    assert client.count("x") == 7
    assert clock.naps == []


def test_stop_ends_a_long_pause_at_once():
    client = PubTatorClient(window=QuietHours(), now=lambda: FRIDAY_AFTERNOON)
    client.stop()
    start = time.monotonic()
    with pytest.raises(Stopped):
        client.count("x")
    assert time.monotonic() - start < 1


def test_quiet_hours_can_outlast_an_outage(make_client):
    api = FakeApi({"x": 7}, {"x": [503] * 30})
    assert make_client(api, patience=3600).count("x") == 7
