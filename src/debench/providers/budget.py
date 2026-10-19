"""Spend and request caps for the paid remote provider, plus the resume cache.

Token estimate: before an attempt the real token count is unknown, so the reservation uses
``ceil(utf8_bytes / 2)`` of the request body. Subword tokenizers emit at least one token per
roughly two bytes only in the worst case (dense Thai or code), so the estimate is deliberately
conservative: the caps are never exceeded even when the estimate is high. After a successful
response the reservation is replaced by the actual ``usage.input_tokens``.

The ledger holds request hashes, model ids, attempt numbers, status, token counts and cost. It
never holds bodies, headers or keys.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from debench.config import ROOT

DEFAULT_LEDGER = ROOT / "results" / "remote_ledger.jsonl"
DEFAULT_CACHE = ROOT / "results" / "raw" / "jev_cache.jsonl"

# Statuses written to the ledger.
STATUS_OK = "ok"
STATUS_REFUSED = "budget_refused"


def estimate_input_tokens(body_bytes: int) -> int:
    """Conservative upper estimate: one token per two UTF-8 bytes."""
    return max(1, math.ceil(body_bytes / 2))


class BudgetExhausted(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Reservation:
    request_hash: str
    model: str
    attempt: int
    est_tokens: int
    est_usd: float
    settled: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RemoteBudget:
    """Thread- and async-safe cap on paid requests (attempts) and spend.

    No await happens while the lock is held, so a plain threading lock is enough for both.
    Every network attempt, including retries, consumes one request. Spend from earlier runs in the
    same ledger counts toward the caps, so a resumed run cannot silently exceed them.
    """

    def __init__(
        self,
        max_requests: int,
        max_usd: float,
        usd_per_million_input_tokens: float,
        ledger_path: Path | str | None = DEFAULT_LEDGER,
        resume: bool = True,
    ):
        if usd_per_million_input_tokens <= 0:
            raise ValueError("usd_per_million_input_tokens must be positive")
        self.max_requests = int(max_requests)
        self.max_usd = float(max_usd)
        self.price = float(usd_per_million_input_tokens)
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self._lock = threading.Lock()
        self.requests_used = 0
        self.usd_spent = 0.0
        self._reserved_usd = 0.0
        if resume and self.ledger_path and self.ledger_path.exists():
            self._load_ledger()

    def _load_ledger(self) -> None:
        assert self.ledger_path is not None
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == STATUS_REFUSED:
                continue
            self.requests_used += 1
            self.usd_spent += float(row.get("usd", 0.0))

    def usd_for(self, tokens: int) -> float:
        return tokens / 1_000_000 * self.price

    def _append(self, row: dict[str, Any]) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def reserve(self, request_hash: str, model: str, attempt: int, body_bytes: int) -> Reservation:
        """Reserve one request and its estimated cost, or raise BudgetExhausted before any attempt."""
        tokens = estimate_input_tokens(body_bytes)
        usd = self.usd_for(tokens)
        with self._lock:
            reason = None
            if self.requests_used + 1 > self.max_requests:
                reason = "max_requests"
            elif self.usd_spent + self._reserved_usd + usd > self.max_usd + 1e-12:
                reason = "max_usd"
            if reason is not None:
                self._append(
                    {
                        "ts": _now(),
                        "request_hash": request_hash,
                        "model": model,
                        "attempt": attempt,
                        "status": STATUS_REFUSED,
                        "input_tokens": 0,
                        "usd": 0.0,
                        "reason": reason,
                    }
                )
                raise BudgetExhausted(reason)
            self.requests_used += 1
            self._reserved_usd += usd
        return Reservation(request_hash, model, attempt, tokens, usd)

    def settle(self, res: Reservation, status: str, input_tokens: int | None) -> float:
        """Replace the reservation with actual usage; ``None`` keeps the conservative estimate.

        Attempts that certainly cost nothing (rate limited, rejected) pass ``input_tokens=0``.
        """
        with self._lock:
            if res.settled:
                return 0.0
            res.settled = True
            tokens = res.est_tokens if input_tokens is None else int(input_tokens)
            usd = self.usd_for(tokens)
            self._reserved_usd -= res.est_usd
            self.usd_spent += usd
            self._append(
                {
                    "ts": _now(),
                    "request_hash": res.request_hash,
                    "model": res.model,
                    "attempt": res.attempt,
                    "status": status,
                    "input_tokens": tokens,
                    "usd": usd,
                    "estimated": input_tokens is None,
                }
            )
        return usd

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_used": self.requests_used,
                "usd_spent": self.usd_spent,
                "max_requests": self.max_requests,
                "max_usd": self.max_usd,
            }


class JevCache:
    """Successful responses keyed by (request_hash, model), reused only to resume interrupted runs.

    A cached hit is never a latency sample and never counts as a new request.
    """

    def __init__(self, path: Path | str | None = DEFAULT_CACHE):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    self._rows[(row["request_hash"], row["model"])] = row["response"]
                except (json.JSONDecodeError, KeyError):
                    continue

    def get(self, request_hash: str, model: str) -> dict[str, Any] | None:
        with self._lock:
            return self._rows.get((request_hash, model))

    def put(self, request_hash: str, model: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._rows[(request_hash, model)] = response
            if self.path is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": _now(), "request_hash": request_hash, "model": model, "response": response}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def __len__(self) -> int:
        return len(self._rows)
