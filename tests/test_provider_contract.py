"""Provider contract: wire body, blocking, version pinning, secrecy, normalization, local inference."""

from __future__ import annotations

import json

import httpx
import pytest

from debench.config import Settings
from debench.normalize import normalize_response
from debench.providers.base import (
    ERROR_BLOCKED,
    ERROR_VERSION,
    DecisionRequest,
    canonical_json,
)
from debench.providers.budget import JevCache, RemoteBudget
from debench.providers.jev_provider import JevProvider, build_body
from debench.providers.mock_provider import MockProvider
from debench.providers.registry import make_provider

FAKE_KEY = "fake-key-0123456789-must-never-leak"
MODEL = "jev-1.14.0"

REQUEST = DecisionRequest(
    scenario_id="s1",
    language="th",
    state={"log": "คอลัมน์ customer_id ไม่มี", "n": 3},
    questions={
        "family": {"type": "choice", "instructions": "ประเภท?", "criteria": {"schema_mismatch": "ก", "other": "ข"}},
        "severity": {"type": "score", "instructions": "รุนแรง?", "criteria": ["ต่ำ", "กลาง", "สูง"]},
        "human": {"type": "noul", "instructions": "ต้องตรวจสอบ?"},
    },
)


def settings(remote: bool = True, **extra: str) -> Settings:
    values = {"TYPESAFE_API_KEY": FAKE_KEY if remote else "", "ALLOW_REMOTE_API": "1" if remote else "0"}
    values.update(extra)
    return Settings(values=values)


def jev_payload(model: str = MODEL) -> dict:
    return {
        "model": model,
        "answers": {
            "family": {
                "type": "choice",
                "choice": "schema_mismatch",
                "probabilities": {"schema_mismatch": 0.8, "other": 0.2},
                "confidence": 0.6,
            },
            "severity": {
                "type": "score",
                "score": 1.1,
                "legend": {"0": "a", "1": "b", "2": "c"},
                "probabilities": {"0": 0.2, "1": 0.5, "2": 0.3},
                "confidence": 0.3,
            },
            "human": {"type": "noul", "noul": 0.7},
        },
        "usage": {"input_tokens": 120, "output_tokens": 0},
    }


def make_jev(tmp_path, handler, *, remote: bool = True, model: str = MODEL, **kw) -> JevProvider:
    return JevProvider(
        "jev_114",
        model,
        settings(remote, **kw.pop("extra", {})),
        transport=httpx.MockTransport(handler),
        budget=RemoteBudget(100, 1.0, 0.042, ledger_path=tmp_path / "ledger.jsonl"),
        cache=JevCache(tmp_path / "cache.jsonl"),
        **kw,
    )


async def test_normalizer_on_jev_shaped_answers(tmp_path):
    provider = make_jev(tmp_path, lambda r: httpx.Response(200, json=jev_payload()))
    resp = await provider.evaluate(REQUEST)
    assert resp.ok and resp.resolved_model == MODEL
    rows = normalize_response(REQUEST, resp)
    assert all(r.valid for r in rows)
    by = {r.question_id: r for r in rows}
    assert by["family"].predicted_label == "schema_mismatch"
    assert by["severity"].predicted_level == 1
    assert by["human"].noul_probability == pytest.approx(0.7)


def test_normalizer_on_laya_shaped_answers_rounded_4dp():
    # Laya-shaped: 4 dp probabilities that sum to 1 only up to rounding, no noul confidence.
    answers = {
        "family": {
            "type": "choice",
            "choice": "schema_mismatch",
            "probabilities": {"schema_mismatch": 0.6667, "other": 0.3334},
            "confidence": 0.9,
            "answer_confidence": 0.66,
        },
        "severity": {
            "type": "score",
            "score": 1.0,
            "probabilities": {"0": 0.3333, "1": 0.3333, "2": 0.3334},
            "confidence": 0.1,
        },
        "human": {"type": "noul", "noul": 0.2501},
    }
    from debench.providers.base import DecisionResponse

    resp = DecisionResponse("laya_routed", "laya:english@7b928d828b7b", answers, 1.0, 10, 0, None, {})
    assert all(r.valid for r in normalize_response(REQUEST, resp))


def test_body_is_byte_identical_to_shared_body():
    body = build_body(REQUEST, MODEL).decode("utf-8")
    shared = canonical_json(REQUEST.shared_body())
    assert body == '{"model":"jev-1.14.0",' + shared[1:]
    parsed = json.loads(body)
    assert parsed["model"] == MODEL
    assert parsed["state"] == REQUEST.state and parsed["questions"] == REQUEST.questions
    assert shared[1:-1] in body  # state and questions text appear verbatim, Thai unescaped
    assert "คอลัมน์" in body


async def test_wire_request_is_exact(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json=jev_payload())

    provider = make_jev(tmp_path, handler)
    await provider.evaluate(REQUEST)
    assert seen["body"] == build_body(REQUEST, MODEL)
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == f"Bearer {FAKE_KEY}"


async def test_blocked_without_network(tmp_path):
    def handler(request):  # pragma: no cover - must never run
        raise AssertionError("network touched while blocked")

    for remote_settings in (settings(False), Settings(values={"ALLOW_REMOTE_API": "0", "TYPESAFE_API_KEY": FAKE_KEY}),
                            Settings(values={"ALLOW_REMOTE_API": "1"})):
        provider = JevProvider(
            "jev_114",
            MODEL,
            remote_settings,
            transport=httpx.MockTransport(handler),
            budget=RemoteBudget(5, 1.0, 0.042, ledger_path=tmp_path / "l.jsonl"),
            cache=JevCache(None),
        )
        resp = await provider.evaluate(REQUEST)
        assert resp.error == ERROR_BLOCKED and resp.answers == {}
        assert provider._client is None
    assert not (tmp_path / "l.jsonl").exists()


@pytest.mark.parametrize("alias", ["jev-latest", "jev-preview", "JEV-LATEST", "jev-1.14", "latest", ""])
def test_aliases_rejected(alias):
    with pytest.raises(ValueError):
        JevProvider("jev_x", alias, settings())


async def test_version_mismatch(tmp_path):
    provider = make_jev(tmp_path, lambda r: httpx.Response(200, json=jev_payload("jev-1.13.0")))
    resp = await provider.evaluate(REQUEST)
    assert resp.error == ERROR_VERSION
    assert resp.resolved_model == "jev-1.13.0"
    assert resp.answers == {}
    assert provider.cache.get(REQUEST.request_hash(), MODEL) is None


async def test_no_secret_anywhere(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("systemone") and not hasattr(handler, "n"):
            handler.n = 1
            return httpx.Response(429, headers={"retry-after-ms": "5"}, text="slow down")
        return httpx.Response(200, json=jev_payload())

    async def no_sleep(_):
        return None

    provider = make_jev(tmp_path, handler, sleep=no_sleep)
    resp = await provider.evaluate(REQUEST)
    assert resp.ok

    def boom(request):
        raise RuntimeError(f"transport failure {request.headers['authorization']}")

    failing = make_jev(tmp_path, boom, sleep=no_sleep)
    bad = await failing.evaluate(DecisionRequest("s2", "en", "other state", REQUEST.questions))
    assert bad.error is not None

    blob = json.dumps([resp.metadata, bad.metadata, bad.error, repr(settings())], default=str)
    blob += (tmp_path / "ledger.jsonl").read_text() + (tmp_path / "cache.jsonl").read_text()
    assert FAKE_KEY not in blob
    assert FAKE_KEY not in json.dumps(settings().redacted())
    assert "Bearer" not in blob


async def test_mock_is_marked_and_deterministic():
    a = await MockProvider(7).evaluate(REQUEST)
    b = await MockProvider(7).evaluate(REQUEST)
    c = await MockProvider(8).evaluate(REQUEST)
    assert a.metadata == {"mock": True} and a.resolved_model == "mock" and a.provider == "mock"
    assert a.answers == b.answers and a.answers != c.answers
    assert all(r.valid for r in normalize_response(REQUEST, a))


def test_registry_tracks():
    assert make_provider("mock", settings(False)).name == "mock"
    p = make_provider("jev_114", settings(False))
    assert p.name == "jev_114" and p.model == MODEL
    assert make_provider("jev_113", settings(False)).model == "jev-1.13.0"
    assert make_provider("laya_en_fixed", settings(False), device="cpu").name == "laya_en_fixed"
    with pytest.raises(ValueError):
        make_provider("rules_only", settings(False))


@pytest.mark.laya
async def test_laya_real_inference_all_tracks():
    from debench.providers.laya_provider import LayaProvider, default_revisions

    revs = default_revisions()
    en = DecisionRequest("t1", "en", "The row count dropped abnormally after the latest batch load.", REQUEST.questions)
    th = DecisionRequest("t1", "th", REQUEST.state, REQUEST.questions)
    for track, expected in (("laya_en_fixed", "english"), ("laya_multi_fixed", "multilingual")):
        resp = await LayaProvider(track, device="cuda", revision=revs).evaluate(en)
        assert resp.ok, resp.metadata
        assert resp.resolved_model == f"laya:{expected}@{revs[expected][:12]}"
        assert resp.metadata["routing"]["model"] == expected
        assert resp.usage_output_tokens == 0
        assert all(r.valid for r in normalize_response(en, resp))
    routed = LayaProvider("laya_routed", device="cuda", revision=revs)
    assert (await routed.evaluate(en)).metadata["routed_checkpoint"] == "english"
    resp_th = await routed.evaluate(th)
    assert resp_th.metadata["routed_checkpoint"] == "multilingual"
    assert resp_th.resolved_model.startswith("laya:multilingual@")
