"""Golden numbers, verified against the raw rows before the analyzers existed.

These are not regression snapshots of whatever the code happened to produce -
each value was independently recomputed from data/ first. If one of these
fails, the analyzer is wrong, not the test.
"""

import pytest

from engine.analyzers import collateral, lookthrough
from engine.analyzers.collateral import CLIENT_DRIVEN, MARKET_DRIVEN
from engine.loader import load
from engine.models import Fact


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def lookthrough_facts(ds) -> dict[str, Fact]:
    return {f.fact_id: f for f in lookthrough.run(ds, ("CL-0002",))}


@pytest.fixture(scope="module")
def collateral_facts(ds) -> dict[str, Fact]:
    return {f.fact_id: f for f in collateral.run(ds, ("CL-0002",))}


# --- underlying_reference parsing -----------------------------------------

def test_parses_single_underlying():
    assert lookthrough.parse_underlying(
        "Single underlying: Helios Cloud Systems Inc"
    ) == ["Helios Cloud Systems Inc"]


def test_parses_worst_of_basket():
    assert lookthrough.parse_underlying(
        "Worst-of basket: Pacific Orient Shipping / Global Energy Majors ADR "
        "/ Bara Nusantara Energy"
    ) == ["Pacific Orient Shipping", "Global Energy Majors ADR", "Bara Nusantara Energy"]


def test_parses_underlying_with_trailing_terms():
    assert lookthrough.parse_underlying(
        "Underlying: XAU spot, 100% capital protection at maturity, 70% participation"
    ) == ["XAU spot"]


def test_returns_nothing_for_a_terms_description():
    # A funding round is not an exposure. Silence beats invention.
    assert lookthrough.parse_underlying(
        "Series D preference shares, last priced round Sep-2025"
    ) == []


def test_returns_nothing_for_blank():
    assert lookthrough.parse_underlying(None) == []
    assert lookthrough.parse_underlying("") == []


def test_normalisation_folds_the_known_suffix_mismatch():
    """The dataset's own artefact: basket string vs instrument name."""
    assert lookthrough.normalise_name("Pacific Orient Shipping") == \
           lookthrough.normalise_name("Pacific Orient Shipping Ltd")


def test_normalisation_keeps_distinct_names_distinct():
    assert lookthrough.normalise_name("Helios Cloud Systems Inc") != \
           lookthrough.normalise_name("Meridian Semiconductor Corp")


# --- GOLDEN: CL-0002 Helios look-through ----------------------------------

def test_helios_combined_exposure_is_24_64_pct(lookthrough_facts):
    fact = lookthrough_facts["F-CL0002-LOOKTHRU-PF-0003"]
    assert fact.numbers["combined_pct"] == pytest.approx(24.6400, abs=1e-4)


def test_helios_splits_14_0035_direct_and_10_6366_via_eln(lookthrough_facts):
    fact = lookthrough_facts["F-CL0002-LOOKTHRU-PF-0003"]
    assert fact.numbers["direct_pct"] == pytest.approx(14.0035, abs=1e-4)
    assert fact.numbers["indirect_pct"] == pytest.approx(10.6366, abs=1e-4)


def test_helios_fact_cites_both_the_direct_row_and_the_eln_row(lookthrough_facts):
    refs = {s.row_ref for s in lookthrough_facts["F-CL0002-LOOKTHRU-PF-0003"].sources}
    assert refs == {
        "PF-0003|SYN-ST-0103|2026-08-26",   # direct equity
        "PF-0003|SYN-SP-0502|2026-08-26",   # the ELN
    }


def test_custody_account_is_100_pct_one_position(lookthrough_facts):
    fact = lookthrough_facts["F-CL0002-CONCENTRATION-PF-0004"]
    assert fact.numbers["combined_pct"] == pytest.approx(100.0, abs=1e-6)
    assert fact.numbers["combined_base"] == pytest.approx(31_920_000.0)


# --- GOLDEN: CF-0001 LTV path and attribution -----------------------------

EXPECTED_LTV = [63.3164, 59.7213, 61.6785, 75.6372, 73.7061]


def test_ltv_series_matches_the_verified_path(ds):
    facility = ds.credit_facilities.set_index("facility_id").loc["CF-0001"]
    path = collateral.ltv_path(facility)
    assert [round(point.ltv, 4) for point in path] == EXPECTED_LTV


def test_ltv_is_recomputed_not_read_from_the_shipped_column(ds):
    """Our value must agree with ltv_pct_<date> to rounding, without using it."""
    facility = ds.credit_facilities.set_index("facility_id").loc["CF-0001"]
    for point in collateral.ltv_path(facility):
        shipped = float(facility[f"ltv_pct_{point.snapshot}"])
        assert point.ltv == pytest.approx(shipped, abs=0.005)


def test_breach_is_found_at_the_june_snapshot(collateral_facts):
    fact = collateral_facts["F-CL0002-COLLAT-BREACH"]
    assert fact.as_of == "2026-06-30"
    assert fact.numbers["ltv_pct"] == pytest.approx(75.6372, abs=1e-4)
    assert fact.numbers["trigger_pct"] == 75.0


def test_breach_is_attributed_to_client_action_not_the_market(collateral_facts):
    """The correction that mattered: the megacap drawdown did not cause this."""
    fact = collateral_facts["F-CL0002-COLLAT-DRIVER"]
    assert CLIENT_DRIVEN in fact.headline
    assert fact.numbers["draw_effect_pp"] == pytest.approx(21.84, abs=0.01)
    assert fact.numbers["market_effect_pp"] == pytest.approx(-5.82, abs=0.01)


def test_the_market_effect_was_helpful_over_the_breach_window(collateral_facts):
    """Lending value rose. An analyzer blaming the market here is wrong."""
    fact = collateral_facts["F-CL0002-COLLAT-DRIVER"]
    assert fact.numbers["market_effect_pp"] < 0
    assert fact.numbers["lending_change"] > 0


def test_the_drawdown_matches_the_rm_note_to_the_dollar(collateral_facts):
    """Note N-004: 'Drew a further USD 1.7m.' Two files, one number."""
    fact = collateral_facts["F-CL0002-COLLAT-DRIVER"]
    assert fact.numbers["drawn_change"] == pytest.approx(1_700_000.0)


def test_current_ltv_is_still_close_to_the_trigger(collateral_facts):
    fact = collateral_facts["F-CL0002-COLLAT-CURRENT"]
    assert fact.numbers["ltv_pct"] == pytest.approx(73.7061, abs=1e-4)
    assert fact.numbers["distance_pp"] == pytest.approx(1.2939, abs=1e-4)


def test_a_market_driven_move_is_labelled_as_such(ds):
    """CF-0005 is the mirror case: cured with drawn unchanged."""
    facility = ds.credit_facilities.set_index("facility_id").loc["CF-0005"]
    path = collateral.ltv_path(facility)
    cure = collateral.steps(path)[1]      # 2026-02-27 -> 2026-03-31
    assert cure.driver == MARKET_DRIVEN
    assert cure.frm.drawn == cure.to.drawn


# --- every fact still satisfies the contract -------------------------------

def test_all_phase1_facts_carry_sources(lookthrough_facts, collateral_facts):
    for fact in list(lookthrough_facts.values()) + list(collateral_facts.values()):
        assert len(fact.sources) >= 1
