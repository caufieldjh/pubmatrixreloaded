import time

import httpx
import pytest

from conftest import FakeApi
from pubmatrix.client import Cache, PubTatorClient, PubTatorError, QueryError, RateLimiter


def test_retries_rate_limit_and_server_errors(make_client):
    api = FakeApi({"x": 7}, {"x": [429, 503]})
    assert make_client(api).count("x") == 7
    assert len(api.searches()) == 3


def test_gives_up_after_retries(make_client):
    api = FakeApi(failures={"x": [500] * 10})
    with pytest.raises(PubTatorError, match="HTTP 500"):
        make_client(api, retries=2).count("x")
    assert len(api.searches()) == 3


def test_bad_query_is_not_retried(make_client):
    api = FakeApi(failures={"x": [400]})
    with pytest.raises(QueryError):
        make_client(api).count("x")
    assert len(api.searches()) == 1


def test_retry_after_header_is_honored():
    slept = []
    replies = iter([httpx.Response(429, headers={"Retry-After": "11"}),
                    httpx.Response(200, json={"count": 1})])
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
    cache.put("k", 1)
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
