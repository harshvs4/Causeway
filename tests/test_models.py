"""The two invariants. If these fail, the governance claim is not true."""

import pytest
from pydantic import ValidationError

from engine.models import Fact, Source, claimed_numbers

# pydantic wraps any ValueError raised inside a validator (including our
# NumberNotSourced) into ValidationError, so that is what callers actually see.
UNSOURCED = "no value in numbers renders to it"

SOURCE = Source(
    file="holdings.csv",
    row_ref="PF-0003|SYN-ST-0103|2026-08-26",
    fields=("market_value_base", "weight_pct"),
)


def make(**overrides):
    kwargs = dict(
        fact_id="F-CL0002-TEST-001",
        client_id="CL-0002",
        kind="lookthrough",
        headline="Helios exposure is 24.64% of PF-0003.",
        detail="",
        numbers={"pct": 24.6400},
        sources=(SOURCE,),
        as_of="2026-08-26",
        confidence="verified",
        severity=70,
    )
    kwargs.update(overrides)
    return Fact(**kwargs)


# --- INVARIANT 1: every fact carries a source ------------------------------

def test_fact_requires_at_least_one_source():
    with pytest.raises(ValueError):
        make(sources=())


def test_fact_with_a_source_is_constructible():
    assert make().sources[0].file == "holdings.csv"


def test_source_requires_at_least_one_field():
    with pytest.raises(ValueError):
        Source(file="holdings.csv", row_ref="PF-0003", fields=())


# --- INVARIANT 2: every rendered number was computed -----------------------

def test_rejects_a_number_that_is_not_in_numbers():
    with pytest.raises(ValidationError, match=UNSOURCED):
        make(headline="Helios exposure is 31.20% of PF-0003.")


def test_accepts_a_number_present_in_numbers():
    assert make(headline="Exposure is 24.64%.").numbers["pct"] == 24.64


def test_accepts_a_legitimately_rounded_rendering():
    # 24.6400 rendered as 24.6 is rounding, which is honest.
    assert make(headline="Exposure is roughly 24.6%.")


def test_rejects_a_rescaled_rendering():
    # 1_700_000 rendered as "1.7" is where a plausible wrong number sneaks in;
    # the template must pass the scaled value through `numbers` explicitly.
    with pytest.raises(ValidationError, match=UNSOURCED):
        make(headline="He drew a further USD 1.7m.", numbers={"drawn": 1_700_000.0})


def test_accepts_a_rescaled_rendering_when_declared():
    assert make(
        headline="He drew a further USD 1.7m.",
        numbers={"drawn": 1_700_000.0, "drawn_musd": 1.7},
    )


def test_detail_is_checked_too():
    with pytest.raises(ValidationError, match=UNSOURCED):
        make(detail="LTV reached 75.64%.")


def test_identifiers_are_not_treated_as_claims():
    # CL-0002, SYN-SP-0505, N-004, PF-0003 must not require sourcing.
    assert make(
        headline="CL-0002 holds SYN-SP-0505 in PF-0003 per note N-004.",
        numbers={},
    )


def test_iso_dates_are_not_treated_as_claims():
    assert make(headline="Breached on 2026-06-30.", numbers={})


def test_thousands_separators_are_understood():
    assert make(
        headline="Drawn rose to USD 6,500,000.",
        numbers={"drawn": 6_500_000.0},
    )


def test_claimed_numbers_scrubs_identifiers_and_dates():
    found = claimed_numbers("CL-0002 breached at 75.64% on 2026-06-30")
    assert [value for _, value, _ in found] == [75.64]


# --- other guards ----------------------------------------------------------

def test_scenario_kind_may_not_be_filed_as_verified():
    with pytest.raises(ValueError):
        make(kind="scenario", confidence="verified", headline="Brent to 140.",
             numbers={"brent": 140.0})


def test_scenario_fact_is_not_vault_verified_eligible():
    fact = make(kind="scenario", confidence="scenario", headline="Brent to 140.",
                numbers={"brent": 140.0})
    assert fact.is_verified is False


def test_verified_fact_is_vault_eligible():
    assert make().is_verified is True


def test_as_of_must_be_iso():
    with pytest.raises(ValueError):
        make(as_of="26/08/2026")


def test_severity_is_bounded():
    with pytest.raises(ValueError):
        make(severity=101)
