from __future__ import annotations

from collections import defaultdict

import pytest

from conftest import CASES, built_records_or_skip
from debench.dataset.paired_generator import validate_pair


@pytest.mark.parametrize("case", CASES)
def test_every_scenario_has_an_equivalent_en_th_pair(case):
    records = built_records_or_skip(case)
    pairs = defaultdict(dict)
    for rec in records:
        pairs[rec.pair_id][rec.language] = rec
    assert len(pairs) == 300
    for pid, by_lang in pairs.items():
        assert set(by_lang) == {"en", "th"}, pid
        en, th = by_lang["en"], by_lang["th"]
        # Gold, split and evidence are language-invariant.
        assert en.gold == th.gold and en.split == th.split and en.group_id == th.group_id
        assert en.evidence_ref == th.evidence_ref and en.protected_tokens == th.protected_tokens
        assert validate_pair(en.state, th.state, en.protected_tokens) == [], pid
        assert en.semantic_pair_validated and th.semantic_pair_validated


@pytest.mark.parametrize("case", CASES)
def test_splits_are_grouped_and_sized(case):
    records = [r for r in built_records_or_skip(case) if r.language == "en"]
    groups = defaultdict(set)
    for r in records:
        groups[r.group_id].add(r.split)
    assert all(len(s) == 1 for s in groups.values()), "a template group crosses splits"
    test = [r for r in records if r.split == "test"]
    assert 190 <= len(test) <= 210
    assert sum(r.p4_subset for r in records) == 50
    assert all(r.split == "test" for r in records if r.p4_subset)
