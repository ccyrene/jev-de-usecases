"""Remote Jev adapter over the TypeSafe HTTP API, using httpx directly.

The SDK is not used: its default model is a moving alias and it retries on its own, which would
break the pinned-version and spend-cap rules. Every attempt here is bounded, counted and ledgered.

Offline by default: unless ``Settings.remote_enabled`` is true, ``evaluate`` returns a BLOCKED
response without constructing a client. The key is read through ``Settings.secret`` only when a
request is actually sent, and it is never placed in metadata, exceptions or the ledger.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from debench.config import Settings
from debench.providers.base import (
    ERROR_AUTH,
    ERROR_BLOCKED,
    ERROR_BUDGET,
    ERROR_EXCEPTION,
    ERROR_HTTP,
    ERROR_INVALID_OUTPUT,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_CONTEXT,
    ERROR_VERSION,
    DecisionRequest,
    DecisionResponse,
    canonical_json,
)
from debench.providers.budget import (
    DEFAULT_CACHE,
    DEFAULT_LEDGER,
    BudgetExhausted,
    JevCache,
    RemoteBudget,
)

FORBIDDEN_ALIASES = frozenset({"jev-latest", "jev-preview"})
DEFAULT_BASE_URL = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"
RETRYABLE_STATUS = frozenset({429, 529})
LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"
BACKOFF_BASE_S = 1.0
RETRY_WAIT_CAP_S = 30.0

Sleep = Callable[[float], Awaitable[None]]


def build_body(request: DecisionRequest, model: str) -> bytes:
    """UTF-8 JSON body whose state/questions text is byte-identical to the shared canonical body.

    ``"model"`` is spliced in directly after the opening brace, so
    ``body == b'{"model":<m>,' + canonical_json(request.shared_body())[1:]``.
    """
    shared = canonical_json(request.shared_body())
    return (canonical_json({"model": model})[:-1] + "," + shared[1:]).encode("utf-8")


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Server-suggested wait in seconds from ``retry-after-ms`` or ``retry-after``, else None."""
    ms = headers.get("retry-after-ms")
    if ms:
        try:
            return max(0.0, float(ms) / 1000.0)
        except ValueError:
            pass
    ra = headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                dt = parsedate_to_datetime(ra)
                return max(0.0, dt.timestamp() - time.time())
            except (TypeError, ValueError):
                return None
    return None


class JevProvider:
    def __init__(
        self,
        track: str,
        model: str,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        budget: RemoteBudget | None = None,
        cache: JevCache | None = None,
        sleep: Sleep | None = None,
        rng: random.Random | None = None,
        long_context: bool = False,
    ):
        if model.strip().lower() in FORBIDDEN_ALIASES or not model.startswith("jev-"):
            raise ValueError("a pinned jev-X.Y.Z model id is required; aliases are not allowed")
        parts = model.removeprefix("jev-").split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError("a pinned jev-X.Y.Z model id is required; aliases are not allowed")
        self.name = track
        self.model = model
        self.settings = settings
        self.transport = transport
        self.long_context = long_context
        self.base_url = settings.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = settings.get_float("JEV_TIMEOUT_S", 60.0)
        self.max_retries = settings.get_int("JEV_MAX_RETRIES", 3)
        self.retry_cap_s = settings.get_float("JEV_RETRY_MAX_WAIT_S", RETRY_WAIT_CAP_S)
        self.budget = budget or RemoteBudget(
            max_requests=settings.get_int("JEV_MAX_REQUESTS", 12000),
            max_usd=settings.get_float("JEV_MAX_USD", 5.0),
            usd_per_million_input_tokens=settings.get_float("JEV_USD_PER_MILLION_INPUT_TOKENS", 0.042),
            ledger_path=DEFAULT_LEDGER,
        )
        self.cache = cache if cache is not None else JevCache(DEFAULT_CACHE)
        self._sleep: Sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ helpers
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(self.timeout_s, connect=min(10.0, self.timeout_s))
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self.transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.secret('TYPESAFE_API_KEY')}",
            "Content-Type": "application/json",
        }
        if self.long_context:
            headers[LONG_CONTEXT_HEADER] = "true"
        return headers

    def _backoff(self, retry_index: int, hint: float | None) -> float:
        if hint is not None:
            return min(self.retry_cap_s, hint)
        delay = min(self.retry_cap_s, BACKOFF_BASE_S * (2**retry_index))
        return min(self.retry_cap_s, delay * (1.0 + 0.25 * self._rng.random()))

    def _fail(
        self,
        error: str,
        t0: float,
        meta: dict[str, Any],
        resolved: str | None = None,
    ) -> DecisionResponse:
        meta["total_wall_ms"] = (time.perf_counter() - t0) * 1000.0
        return DecisionResponse(
            provider=self.name,
            resolved_model=resolved or self.model,
            answers={},
            wall_ms=meta["total_wall_ms"],
            usage_input_tokens=None,
            usage_output_tokens=None,
            error=error,
            metadata=meta,
        )

    # ------------------------------------------------------------------ evaluate
    async def evaluate(self, request: DecisionRequest) -> DecisionResponse:
        meta: dict[str, Any] = {
            "model_requested": self.model,
            "attempts": 0,
            "retry_wait_ms": 0.0,
            "cached": False,
            "status_codes": [],
            "base_url": self.base_url,
            "timeout_s": self.timeout_s,
        }
        t0 = time.perf_counter()
        if not self.settings.remote_enabled:
            meta["blocked_reason"] = "ALLOW_REMOTE_API is not 1 or no API key is configured"
            return self._fail(ERROR_BLOCKED, t0, meta)

        req_hash = request.request_hash()
        body = build_body(request, self.model)
        meta["request_hash"] = req_hash
        meta["request_bytes"] = len(body)

        hit = self.cache.get(req_hash, self.model)
        if hit is not None:
            # Resumed run: reuse, never a latency sample and never a new request.
            meta.update(cached=True, latency_valid=False)
            usage = hit.get("usage") or {}
            return DecisionResponse(
                provider=self.name,
                resolved_model=str(hit.get("model", self.model)),
                answers=hit.get("answers", {}),
                wall_ms=0.0,
                usage_input_tokens=usage.get("input_tokens"),
                usage_output_tokens=usage.get("output_tokens"),
                error=None,
                metadata=meta,
            )

        total_attempts = self.max_retries + 1
        last_error = ERROR_EXCEPTION
        for attempt in range(1, total_attempts + 1):
            try:
                res = self.budget.reserve(req_hash, self.model, attempt, len(body))
            except BudgetExhausted as exc:
                meta["budget_reason"] = exc.reason
                return self._fail(ERROR_BUDGET, t0, meta)
            meta["attempts"] = attempt
            hint: float | None = None
            t_attempt = time.perf_counter()
            try:
                resp = await self._get_client().post(ENDPOINT, content=body, headers=self._headers())
            except httpx.TimeoutException:
                self.budget.settle(res, "timeout", None)  # may have been billed; keep the estimate
                last_error = ERROR_TIMEOUT
                meta["status_codes"].append("timeout")
            except httpx.TransportError as exc:
                self.budget.settle(res, "transport_error", 0)
                last_error = ERROR_HTTP
                meta["status_codes"].append("transport_error")
                meta["transport_error_type"] = type(exc).__name__
            except Exception as exc:  # noqa: BLE001 - recorded by type name only
                self.budget.settle(res, "exception", None)
                meta["exception_type"] = type(exc).__name__
                return self._fail(ERROR_EXCEPTION, t0, meta)
            else:
                meta["final_attempt_ms"] = (time.perf_counter() - t_attempt) * 1000.0
                code = resp.status_code
                meta["status_codes"].append(code)
                if code == 200:
                    return self._finish_ok(resp, res, req_hash, t0, meta)
                self.budget.settle(res, f"http_{code}", 0)
                if code in RETRYABLE_STATUS:
                    last_error = ERROR_RATE_LIMITED
                    hint = parse_retry_after(resp.headers)
                elif code >= 500:
                    last_error = ERROR_HTTP
                    hint = parse_retry_after(resp.headers)
                else:
                    return self._fail(self._client_error(code, resp), t0, meta)
            if attempt < total_attempts:
                wait = self._backoff(attempt - 1, hint)
                meta["retry_wait_ms"] += wait * 1000.0
                await self._sleep(wait)
        return self._fail(last_error, t0, meta)

    @staticmethod
    def _client_error(code: int, resp: httpx.Response) -> str:
        if code in (401, 403):
            return ERROR_AUTH
        if code == 413:
            return ERROR_UNSUPPORTED_CONTEXT
        if code == 400:
            try:
                err = resp.json().get("error")
                text = err.get("code", "") if isinstance(err, dict) else str(err or "")
            except (ValueError, AttributeError):
                text = ""
            if "max_tokens_exceeded" in text or "max_tokens_exceeded" in resp.text[:500]:
                return ERROR_UNSUPPORTED_CONTEXT
        return ERROR_HTTP

    def _finish_ok(self, resp: httpx.Response, res: Any, req_hash: str, t0: float, meta: dict[str, Any]) -> DecisionResponse:
        try:
            data = resp.json()
            answers = data["answers"]
            usage = data.get("usage") or {}
            reported = str(data["model"])
            if not isinstance(answers, dict):
                raise TypeError("answers is not an object")
            in_tokens = usage.get("input_tokens")
            in_tokens = int(in_tokens) if isinstance(in_tokens, (int, float)) and not isinstance(in_tokens, bool) else None
        except (ValueError, KeyError, TypeError):
            self.budget.settle(res, "invalid_output", None)
            return self._fail(ERROR_INVALID_OUTPUT, t0, meta)
        self.budget.settle(res, "ok", in_tokens)
        meta["model_reported"] = reported
        if reported != self.model:
            return self._fail(ERROR_VERSION, t0, meta, resolved=reported)
        self.cache.put(req_hash, self.model, {"model": reported, "answers": answers, "usage": usage})
        meta["total_wall_ms"] = (time.perf_counter() - t0) * 1000.0
        out_tokens = usage.get("output_tokens")
        return DecisionResponse(
            provider=self.name,
            resolved_model=reported,
            answers=answers,
            # End to end from the same client, retries and backoff included (never hidden).
            wall_ms=meta["total_wall_ms"],
            usage_input_tokens=in_tokens,
            usage_output_tokens=int(out_tokens) if isinstance(out_tokens, (int, float)) else None,
            error=None,
            metadata=meta,
        )
