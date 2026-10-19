"""Shared helpers for building paired EN/TH datasets: grouped splits, pair checks, ids."""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from typing import Any

from debench.dataset.schema import SPLIT_SIZES, SPLITS, ScenarioSpec

THAI_DIGITS = re.compile(r"[๐-๙]")
THAI_LETTERS = re.compile(r"[฀-๿]")
# ASCII runs containing a digit: numbers, ids, timestamps, versions (e.g. 2026-10-19T03:00:00Z, 12.5%).
NUMERIC_TOKEN = re.compile(r"[A-Za-z0-9_.:%/\-+]*\d[A-Za-z0-9_.:%/\-+]*")
INTERNAL_MARKERS = ("_internal", "injected", "gold_label", "label_source", "fault_id")


def scenario_id(case: str, index: int) -> str:
    return f"{case}-{index:04d}"


def assign_splits(specs: list[ScenarioSpec], seed: int, sizes: dict[str, int] = SPLIT_SIZES) -> None:
    """Assign dev/calibration/test by whole groups in seeded random order.

    Groups are never split. Each non-final split takes groups in shuffled order while they fit, so it
    hits its target exactly whenever group sizes allow and otherwise falls short; everything left goes
    to the final split. Actual counts go in the dataset card.
    """
    groups: dict[str, list[ScenarioSpec]] = defaultdict(list)
    for spec in specs:
        groups[spec.group_id].append(spec)
    remaining = sorted(groups)
    random.Random(seed).shuffle(remaining)
    for split in SPLITS[:-1]:
        filled, chosen = 0, []
        for gid in remaining:
            if filled + len(groups[gid]) <= sizes[split]:
                chosen.append(gid)
                filled += len(groups[gid])
                if filled == sizes[split]:
                    break
        for gid in chosen:
            remaining.remove(gid)
            for spec in groups[gid]:
                spec.split = split
    for gid in remaining:
        for spec in groups[gid]:
            spec.split = SPLITS[-1]


def p4_subset(test_ids: list[str], seed: int, n: int = 50) -> set[str]:
    """Predeclared cross-lingual robustness subset (P4), drawn once from the test split."""
    ids = sorted(test_ids)
    return set(random.Random(seed + 4).sample(ids, min(n, len(ids))))


def state_text(state: dict[str, Any] | str | list[str]) -> str:
    if isinstance(state, str):
        return state
    if isinstance(state, list):
        return "\n".join(map(str, state))
    parts = []
    for key, val in state.items():
        parts.append(str(key))
        parts.append(state_text(val) if isinstance(val, (dict, list)) else str(val))
    return "\n".join(parts)


def numeric_tokens(text: str) -> Counter:
    return Counter(m.group(0).rstrip(".:-/") for m in NUMERIC_TOKEN.finditer(text))


def validate_pair(
    en_state: dict[str, Any] | str,
    th_state: dict[str, Any] | str,
    protected_tokens: list[str],
) -> list[str]:
    """Return problems that break EN/TH semantic equivalence of machine facts (empty = OK)."""
    problems = []
    en, th = state_text(en_state), state_text(th_state)
    for tok in protected_tokens:
        if tok not in en:
            problems.append(f"protected token missing in en: {tok!r}")
        if tok not in th:
            problems.append(f"protected token missing in th: {tok!r}")
    if THAI_DIGITS.search(th):
        problems.append("Thai digits used; machine numbers must stay ASCII")
    if THAI_LETTERS.search(en):
        problems.append("Thai text inside the English state")
    if not THAI_LETTERS.search(th):
        problems.append("Thai state has no Thai text")
    if numeric_tokens(en) != numeric_tokens(th):
        diff = (numeric_tokens(en) - numeric_tokens(th)) + (numeric_tokens(th) - numeric_tokens(en))
        problems.append(f"numeric tokens differ between en and th: {sorted(diff)}")
    if isinstance(en_state, dict) and isinstance(th_state, dict) and list(en_state) != list(th_state):
        problems.append("state field keys differ between en and th")
    for marker in INTERNAL_MARKERS:
        if marker in en.lower() or marker in th.lower():
            problems.append(f"internal marker {marker!r} leaked into state")
    return problems


def class_balance(specs: list[ScenarioSpec], question_id: str) -> dict[str, int]:
    return dict(Counter(str(s.gold[question_id]) for s in specs))
