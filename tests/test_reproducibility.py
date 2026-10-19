from __future__ import annotations

from dataclasses import asdict

import pytest

from conftest import CASES, case_module_or_skip
from debench import SEED
from debench.dataset.paired_generator import assign_splits


@pytest.mark.parametrize("case", CASES)
def test_generator_is_deterministic_for_a_seed(case):
    mod = case_module_or_skip(case)
    a, b = mod.generate_specs(SEED), mod.generate_specs(SEED)
    assign_splits(a, SEED)
    assign_splits(b, SEED)
    assert [asdict(s) for s in a] == [asdict(s) for s in b]


@pytest.mark.parametrize("case", CASES)
def test_generator_changes_with_seed(case):
    mod = case_module_or_skip(case)
    a, b = mod.generate_specs(SEED), mod.generate_specs(SEED + 1)
    assert [s.facts for s in a] != [s.facts for s in b]


@pytest.mark.parametrize("case", CASES)
def test_rendering_is_deterministic(case):
    mod = case_module_or_skip(case)
    spec = mod.generate_specs(SEED)[0]
    facts = mod.expected_evidence(spec)
    for lang in ("en", "th"):
        assert mod.render_state(spec, facts, lang, "common_fit") == mod.render_state(spec, facts, lang, "common_fit")
