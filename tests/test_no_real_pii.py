"""E05 seed data must be unmistakably fictional: invalid IDs, reserved ranges, example domains."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from debench import SEED
from debench.dataset import e05
from debench.evidence.postgres_pii import mask_value

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
# Standalone 13-digit numbers (plain or dash/space grouped); digit runs inside hex hashes and UUIDs are noise.
DIGIT13 = re.compile(r"(?<![A-Za-z0-9-])\d(?:[- ]?\d){12}(?![A-Za-z0-9-])")
PHONE = re.compile(r"^\+\d[\d ]+$")


@pytest.fixture(scope="module")
def seed():
    specs = e05.generate_specs(SEED)
    return e05.db_seed(specs, SEED)


def _strings(v: Any) -> Iterator[str]:
    if isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str):
        yield v
    elif v is not None:
        yield str(v)


def _all_strings(seed) -> Iterator[tuple[str, str]]:
    for names, rows in seed.rows.values():
        for row in rows:
            for name, cell in zip(names, row):
                if name == "attrs":
                    cell = json.loads(cell)
                for s in _strings(cell):
                    yield name, s


def test_row_total_is_about_ten_thousand(seed):
    assert sum(len(rows) for _, rows in seed.rows.values()) == 10_000


def test_every_thirteen_digit_number_fails_the_thai_checksum(seed):
    seen = 0
    for _, s in _all_strings(seed):
        for m in DIGIT13.finditer(s):
            seen += 1
            assert not e05.is_valid_thai_id(m.group(0)), "valid Thai ID checksum in seed data"
    assert seen > 2000  # national-id, lookalike and order-ref fields are present


def test_all_emails_use_reserved_example_domains(seed):
    seen = 0
    for _, s in _all_strings(seed):
        for m in EMAIL.finditer(s):
            seen += 1
            assert m.group(1) in e05.ALLOWED_EMAIL_DOMAINS
    assert seen > 1000


def test_all_phones_are_in_reserved_fictional_ranges(seed):
    seen = 0
    for _, s in _all_strings(seed):
        if PHONE.match(s):
            seen += 1
            assert any(p.match(s) for p in e05.PHONE_PATTERNS), s
    assert seen > 1000


def test_names_come_only_from_fictional_pools(seed):
    en_names = {f"{g} {s}" for g in e05.GIVEN_EN for s in e05.SURNAME_EN}
    th_names = {f"{g} {s}" for g in e05.GIVEN_TH for s in e05.SURNAME_TH}
    checked = 0
    for fd in e05.FIELD_DEFS:
        gen = e05.ARCHE_BY_KEY[fd.arche].gen
        if gen not in ("name_en", "name_th", "health_note", "free_text"):
            continue
        for v in e05.column_values(SEED + e05.CASE_OFFSET, fd.idx):
            if gen == "name_en":
                assert v in en_names
            elif gen == "name_th":
                assert v in th_names
            elif gen == "health_note":
                assert v.split(": ")[0] in en_names
            else:  # free text may only mention names from the fictional pool
                for pair in re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", v):
                    assert pair in en_names, pair
            checked += 1
    assert checked > 5000


def test_government_like_ids_ips_and_hosts_are_fictional(seed):
    for fd in e05.FIELD_DEFS:
        gen = e05.ARCHE_BY_KEY[fd.arche].gen
        vals = e05.column_values(SEED + e05.CASE_OFFSET, fd.idx)
        if gen == "govid":
            assert all(re.fullmatch(r"XZ\d{7}", v) for v in vals)
        elif gen == "ip":
            assert all(v.rsplit(".", 1)[0] in e05.IP_PREFIXES for v in vals)
        elif gen == "postcode":
            assert all(v.startswith("00") for v in vals)
        elif gen == "address":
            assert all("Example Road" in v and "Sampleburg" in v for v in vals)
        elif gen == "company":
            assert all(v.startswith(e05.COMPANY_A) for v in vals)


def test_catalog_text_carries_no_identifier_values(seed):
    for row in seed.catalog:
        for cell in row:
            if isinstance(cell, str):
                assert not EMAIL.search(cell) and not DIGIT13.search(cell)


def test_masked_samples_never_reveal_a_full_identifier():
    specs = e05.generate_specs(SEED)
    for s in specs:
        facts = e05.expected_evidence(s)
        vals = e05.column_values(s.facts["data_seed"], e05.FIELD_BY_ID[s.facts["field_id"]].idx)
        for rid, masked in zip(facts["sample_row_ids"], facts["samples_masked"]):
            raw = str(vals[rid - 1])
            assert masked != raw and mask_value(raw) == masked
            assert not EMAIL.search(masked) or "*" in masked.split("@")[0] or len(masked.split("@")[0]) < 3
            assert not DIGIT13.search(masked)
            alnum = sum(c.isalnum() for c in raw.split("@")[0])
            kept = sum(1 for a, b in zip(raw.split("@")[0], masked.split("@")[0]) if a == b and a.isalnum())
            if raw.isascii() and alnum <= 24:
                assert kept <= 0.4 * alnum
