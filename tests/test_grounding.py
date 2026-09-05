"""The checker that has to tell a restatement from a fabrication.

Every "supported" case here is a real line a voice agent produced during the
Phase 4 rehearsal probes, against real values from build/facts.json. Every
"unsupported" case is a number nothing in the data can reach.
"""

import json

import pytest

from engine.grounding import claimed, grounded_values, is_supported, ungrounded
from engine.loader import BUILD_DIR

# Real values from CL-0002's facts.
LTV = 75.6372
SELLABLE = 240726.0
DAILY = 13207200.0
SHORTFALL = 1759274.0
HELIOS = 24.6400
ARANYA = 31920000.0
ALLOWED = [LTV, SELLABLE, DAILY, SHORTFALL, HELIOS, ARANYA]


@pytest.mark.parametrize(
    "written, why",
    [
        ("24.64", "exact, as computed"),
        ("24.6", "rounded to one decimal"),
        ("75.64", "LTV as usually written"),
        ("13.2", "13,207,200 said as '13.2 million'"),
        ("240,000", "240,726 said as 'about 240,000'"),
        ("240,726", "exact"),
        ("1.76", "shortfall 1,759,274 said as '1.76 million'"),
        ("31,920,000", "exact"),
        ("31.9", "said as '31.9 million'"),
        ("13,207,200", "exact"),
    ],
)
def test_legitimate_restatements_are_supported(written, why):
    value, decimals = float(written.replace(",", "")), (
        len(written.split(".")[1]) if "." in written else 0
    )
    assert is_supported(value, decimals, ALLOWED), why


@pytest.mark.parametrize("written", ["31.20", "88.4", "999,999", "4.75", "62.10"])
def test_numbers_nothing_can_reach_are_unsupported(written):
    value, decimals = float(written.replace(",", "")), (
        len(written.split(".")[1]) if "." in written else 0
    )
    assert not is_supported(value, decimals, ALLOWED)


def test_the_phase4_transcript_lines_are_now_clean():
    """These three were flagged as ungrounded and were not."""
    lines = [
        "only about USD 240,000 of that portfolio can actually be sold",
        "against 13.2 million that looks liquid on your statement",
        "leaving a shortfall of about USD 1.76 million",
    ]
    for line in lines:
        assert ungrounded(line, ALLOWED) == [], line


def test_a_real_fabrication_is_still_caught():
    said = "My Helios weighting is 31.20% and the stake is worth USD 44,500,000."
    assert set(ungrounded(said, ALLOWED)) == {"31.20", "44,500,000"}


def test_identifiers_and_dates_are_not_claims():
    said = "CF-0001 breached on 2026-06-30, see F-CL0002-COLLAT-BREACH."
    assert ungrounded(said, ALLOWED) == []


def test_small_conversational_integers_are_ignored():
    assert ungrounded("two decimal places, 8 out of 10, in 2027", ALLOWED) == []


def test_a_large_bare_integer_is_still_checked():
    assert ungrounded("worth 44500000 today", ALLOWED) == ["44500000"]


def test_claimed_strips_identifiers():
    assert [v for _, v, _ in claimed("CL-0002 holds 24.64%")] == [24.64]


def test_grounded_values_reads_the_real_build():
    envelope = json.loads((BUILD_DIR / "facts.json").read_text())
    values = grounded_values(envelope["facts"], "CL-0002")
    # Stored values carry full precision (24.64003464...), so membership is
    # approximate by nature - which is exactly why is_supported exists.
    assert any(abs(v - HELIOS) < 1e-3 for v in values)
    assert any(abs(v - LTV) < 1e-3 for v in values)
    assert all(isinstance(v, float) for v in values)
    # and the checker accepts the rounded form a person would actually say
    assert is_supported(24.64, 2, values)
    assert is_supported(75.64, 2, values)


def test_every_headline_the_engine_wrote_checks_out_against_its_own_numbers():
    """A sanity check on the checker: engine prose must never look ungrounded."""
    envelope = json.loads((BUILD_DIR / "facts.json").read_text())
    for fact in envelope["facts"]:
        allowed = list((fact.get("numbers") or {}).values())
        bad = ungrounded(fact["headline"], allowed)
        assert bad == [], f"{fact['fact_id']}: {bad}"
