"""Build providers from configs/models.yaml tracks."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import yaml

from debench.config import CONFIG_DIR, Settings
from debench.providers.base import DecisionProvider
from debench.providers.budget import DEFAULT_LEDGER, RemoteBudget

_LOCK = threading.Lock()
_BUDGETS: dict[tuple, RemoteBudget] = {}

# .env overrides for pinned Jev tracks; the YAML pin is used when these are empty.
_JEV_ENV = {"jev_114": "JEV_MODEL", "jev_113": "JEV_REGRESSION_MODEL"}


def load_models_config() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))


def shared_budget(settings: Settings) -> RemoteBudget:
    """One budget per process and cap set, so every Jev track draws on the same caps."""
    cfg = load_models_config()["jev"]
    price = settings.get_float("JEV_USD_PER_MILLION_INPUT_TOKENS", cfg["usd_per_million_input_tokens"])
    key = (settings.get_int("JEV_MAX_REQUESTS", 12000), settings.get_float("JEV_MAX_USD", 5.0), price)
    with _LOCK:
        if key not in _BUDGETS:
            _BUDGETS[key] = RemoteBudget(key[0], key[1], key[2], ledger_path=DEFAULT_LEDGER)
        return _BUDGETS[key]


def reset_shared_state() -> None:
    with _LOCK:
        _BUDGETS.clear()


def make_provider(
    track: str,
    settings: Settings,
    *,
    device: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    seed: int | None = None,
) -> DecisionProvider:
    cfg = load_models_config()
    spec = cfg["tracks"].get(track)
    if spec is None:
        raise KeyError(f"unknown track {track!r}")
    kind = spec["provider"]
    if kind == "laya":
        from debench.providers.laya_provider import LayaProvider, default_revisions

        return LayaProvider(
            track,
            device=device or settings.get("LAYA_DEVICE", "cpu"),
            revision=default_revisions(settings.values),
        )
    if kind == "jev":
        from debench.providers.jev_provider import JevProvider

        model = settings.get(_JEV_ENV.get(track, ""), "") or spec["model"]
        return JevProvider(track, model, settings, transport=transport, budget=shared_budget(settings))
    if kind == "mock":
        from debench.providers.mock_provider import MockProvider

        return MockProvider(seed if seed is not None else settings.get_int("SEED", 0))
    raise ValueError(f"track {track!r} (provider {kind!r}) is not a model provider built here")
