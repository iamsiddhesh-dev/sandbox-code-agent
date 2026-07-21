"""Fast structural checks on bench/injections.jsonl — no LLM, no sandbox.

Guards the shape of the corpus the live harness (test_injection.py) consumes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parent.parent / "bench" / "injections.jsonl"
CLASSES = {"rule-override", "exfil-code", "sandbox-escape", "secret-disclosure"}
REQUIRED = {"id", "class", "indirect", "request", "data", "expected"}


def load_cases() -> list[dict]:
    lines = [ln for ln in CORPUS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_corpus_has_at_least_twelve_cases():
    assert len(load_cases()) >= 12


def test_every_case_has_required_fields():
    for case in load_cases():
        assert REQUIRED <= set(case), f"{case.get('id')} missing fields"
        assert case["class"] in CLASSES
        assert isinstance(case["indirect"], bool)
        assert case["request"].strip()
        assert case["expected"].strip()


def test_all_four_attack_classes_present():
    seen = {case["class"] for case in load_cases()}
    assert seen == CLASSES


def test_indirect_variants_exist_and_carry_a_payload():
    indirect = [c for c in load_cases() if c["indirect"]]
    assert indirect, "corpus must include indirect (data-embedded) variants"
    for case in indirect:
        assert case["data"], f"{case['id']} is indirect but carries no <data> payload"


def test_ids_are_unique():
    ids = [c["id"] for c in load_cases()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_case_json_roundtrips(case):
    assert json.loads(json.dumps(case)) == case
