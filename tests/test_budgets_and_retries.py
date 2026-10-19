"""Retry, backoff, budget and resume-cache behaviour, all against httpx.MockTransport (no network)."""

from __future__ import annotations

import json
import random

import httpx
import pytest

from debench.config import Settings
from debench.providers.base import (
    ERROR_BUDGET,
    ERROR_AUTH,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_CONTEXT,
    DecisionRequest,
)
from debench.providers.budget import BudgetExhausted, JevCache, RemoteBudget, estimate_input_tokens
from debench.providers.jev_provider import JevProvider, build_body, parse_retry_after

FAKE_KEY = "fake-key-for-budget-tests-do-not-leak"
MODEL = "jev-1.14.0"
REQ = DecisionRequest("s", "en", "state text", {"q": {"type": "noul", "instructions": "ok?"}})
OK = {"model": MODEL, "answers": {"q": {"type": "noul", "noul": 0.4}}, "usage": {"input_tokens": 50, "output_tokens": 0}}


def provider(tmp_path, responses, *, retries=3, max_requests=100, max_usd=1.0, sleeps=None, cache=True):
    calls = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    p = JevProvider(
        "jev_114",
        MODEL,
        Settings(values={"TYPESAFE_API_KEY": FAKE_KEY, "ALLOW_REMOTE_API": "1", "JEV_MAX_RETRIES": str(retries)}),
        transport=httpx.MockTransport(handler),
        budget=RemoteBudget(max_requests, max_usd, 0.042, ledger_path=tmp_path / "ledger.jsonl"),
        cache=JevCache(tmp_path / "cache.jsonl") if cache else JevCache(None),
        sleep=sleep,
        rng=random.Random(1),
    )
    return p, calls


def ledger(tmp_path):
    path = tmp_path / "ledger.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


async def test_429_with_retry_after_then_success(tmp_path):
    sleeps = []
    p, calls = provider(
        tmp_path,
        [httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json=OK)],
        sleeps=sleeps,
    )
    resp = await p.evaluate(REQ)
    assert resp.ok and len(calls) == 2
    assert sleeps == [2.0]
    assert resp.metadata["attempts"] == 2
    assert resp.metadata["retry_wait_ms"] == pytest.approx(2000.0)
    assert resp.metadata["status_codes"] == [429, 200]
    rows = ledger(tmp_path)
    assert [r["status"] for r in rows] == ["http_429", "ok"]
    assert rows[0]["usd"] == 0.0 and rows[1]["input_tokens"] == 50
    assert rows[1]["usd"] == pytest.approx(50 / 1e6 * 0.042)


async def test_retry_after_ms_preferred_and_capped(tmp_path):
    sleeps = []
    p, _ = provider(
        tmp_path,
        [httpx.Response(429, headers={"retry-after-ms": "250", "retry-after": "9"}),
         httpx.Response(429, headers={"retry-after": "9999"}),
         httpx.Response(200, json=OK)],
        sleeps=sleeps,
    )
    resp = await p.evaluate(REQ)
    assert resp.ok
    assert sleeps == [0.25, 30.0]


def test_parse_retry_after_forms():
    assert parse_retry_after(httpx.Headers({"retry-after-ms": "1500"})) == 1.5
    assert parse_retry_after(httpx.Headers({"retry-after": "3"})) == 3.0
    assert parse_retry_after(httpx.Headers({"retry-after": "junk"})) is None
    assert parse_retry_after(httpx.Headers({})) is None


async def test_529_retries_exhausted(tmp_path):
    sleeps = []
    p, calls = provider(tmp_path, [httpx.Response(529)] * 4, retries=3, sleeps=sleeps)
    resp = await p.evaluate(REQ)
    assert resp.error == ERROR_RATE_LIMITED
    assert len(calls) == 4 and resp.metadata["attempts"] == 4
    assert len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2]  # exponential growth dominates the bounded jitter
    assert all(r["usd"] == 0.0 for r in ledger(tmp_path))


async def test_5xx_and_timeout_and_client_errors(tmp_path):
    p, calls = provider(tmp_path, [httpx.Response(503), httpx.Response(200, json=OK)])
    assert (await p.evaluate(REQ)).ok and len(calls) == 2

    p, calls = provider(tmp_path, [httpx.ReadTimeout("t")] * 2, retries=1, cache=False)
    resp = await p.evaluate(REQ)
    assert resp.error == ERROR_TIMEOUT and len(calls) == 2

    p, calls = provider(tmp_path, [httpx.Response(401, json={"error": "bad key"})], cache=False)
    assert (await p.evaluate(REQ)).error == ERROR_AUTH and len(calls) == 1  # never retried
    p, calls = provider(tmp_path, [httpx.Response(403, json={"detail": {"error_type": "authentication_error"}})], cache=False)
    assert (await p.evaluate(REQ)).error == ERROR_AUTH and len(calls) == 1

    p, calls = provider(tmp_path, [httpx.Response(400, json={"error": {"code": "max_tokens_exceeded"}})], cache=False)
    assert (await p.evaluate(REQ)).error == ERROR_UNSUPPORTED_CONTEXT and len(calls) == 1


async def test_budget_refuses_before_exceeding_requests(tmp_path):
    p, calls = provider(tmp_path, [httpx.Response(529)] * 10, retries=5, max_requests=2)
    resp = await p.evaluate(REQ)
    assert resp.error == ERROR_BUDGET
    assert len(calls) == 2  # third attempt refused before it was sent
    assert resp.metadata["budget_reason"] == "max_requests"
    assert ledger(tmp_path)[-1]["status"] == "budget_refused"


async def test_budget_refuses_before_exceeding_usd(tmp_path):
    body_bytes = len(build_body(REQ, MODEL))
    est_usd = estimate_input_tokens(body_bytes) / 1e6 * 0.042
    p, calls = provider(tmp_path, [httpx.Response(200, json=OK)] * 3, max_usd=est_usd * 0.99)
    resp = await p.evaluate(REQ)
    assert resp.error == ERROR_BUDGET and calls == []
    assert resp.metadata["budget_reason"] == "max_usd"


def test_budget_unit_reserve_settle_and_resume(tmp_path):
    path = tmp_path / "l.jsonl"
    b = RemoteBudget(3, 0.001, 1.0, ledger_path=path)  # $1 per million tokens
    r1 = b.reserve("h", MODEL, 1, 1000)  # 500 tokens -> $0.0005
    assert b.snapshot()["requests_used"] == 1
    b.settle(r1, "ok", 100)  # actual 100 tokens -> $0.0001
    assert b.usd_spent == pytest.approx(1e-4)
    with pytest.raises(BudgetExhausted):
        b.reserve("h2", MODEL, 1, 4000)  # 2000 tokens -> $0.002 > cap
    # a resumed run counts earlier spend
    b2 = RemoteBudget(3, 0.001, 1.0, ledger_path=path)
    assert b2.requests_used == 1 and b2.usd_spent == pytest.approx(1e-4)


async def test_concurrent_reservations_never_exceed_cap(tmp_path):
    import asyncio

    b = RemoteBudget(5, 10.0, 0.042, ledger_path=tmp_path / "l.jsonl")

    async def one(i):
        try:
            b.settle(b.reserve(f"h{i}", MODEL, 1, 100), "ok", 10)
            return 1
        except BudgetExhausted:
            return 0

    assert sum(await asyncio.gather(*(one(i) for i in range(20)))) == 5


async def test_cache_reuse_on_resume(tmp_path):
    p, calls = provider(tmp_path, [httpx.Response(200, json=OK)])
    first = await p.evaluate(REQ)
    assert first.ok and not first.metadata["cached"] and len(calls) == 1
    used = p.budget.snapshot()["requests_used"]

    # A new process: fresh provider and budget read the same files.
    p2, calls2 = provider(tmp_path, [])
    second = await p2.evaluate(REQ)
    assert second.ok and second.metadata["cached"] is True
    assert second.metadata["latency_valid"] is False
    assert second.wall_ms == 0.0 and calls2 == []
    assert second.answers == first.answers and second.resolved_model == MODEL
    assert p2.budget.requests_used == used  # reused, not a new request
    assert len(ledger(tmp_path)) == 1


async def test_cache_is_keyed_by_model(tmp_path):
    cache = JevCache(tmp_path / "c.jsonl")
    cache.put("h", "jev-1.13.0", {"model": "jev-1.13.0", "answers": {}, "usage": {}})
    assert cache.get("h", MODEL) is None and cache.get("h", "jev-1.13.0") is not None


async def test_ledger_has_no_key_or_body(tmp_path):
    p, _ = provider(tmp_path, [httpx.Response(429), httpx.Response(200, json=OK)])
    await p.evaluate(REQ)
    text = (tmp_path / "ledger.jsonl").read_text() + (tmp_path / "cache.jsonl").read_text()
    assert FAKE_KEY not in text and "state text" not in (tmp_path / "ledger.jsonl").read_text()
    for row in ledger(tmp_path):
        assert set(row) <= {"ts", "request_hash", "model", "attempt", "status", "input_tokens", "usd", "estimated", "reason"}
        assert row["request_hash"] == REQ.request_hash()
