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


# ===========================================================================
# CL-0001: golden numbers, verified against raw rows before these analyzers
# ===========================================================================

@pytest.fixture(scope="module")
def cl1_lookthrough(ds) -> dict[str, Fact]:
    return {f.fact_id: f for f in lookthrough.run(ds, ("CL-0001",))}


@pytest.fixture(scope="module")
def cl1_collateral(ds) -> dict[str, Fact]:
    return {f.fact_id: f for f in collateral.run(ds, ("CL-0001",))}


@pytest.fixture(scope="module")
def cl1_tension(ds) -> dict[str, Fact]:
    from engine.analyzers import tension
    return {f.fact_id: f for f in tension.run(ds, ("CL-0001",))}


def test_bara_is_97_9683_pct_of_the_custody_account(cl1_lookthrough):
    fact = cl1_lookthrough["F-CL0001-CONCENTRATION-PF-0002"]
    assert fact.numbers["combined_pct"] == pytest.approx(97.9683, abs=1e-4)


def test_bara_true_book_exposure_is_44_9853_pct(cl1_lookthrough):
    """Direct plus the leg reached through the worst-of note."""
    fact = cl1_lookthrough["F-CL0001-BOOKEXPOSURE"]
    assert fact.numbers["book_pct"] == pytest.approx(44.9853, abs=1e-4)
    assert fact.numbers["direct_pct"] == pytest.approx(41.4156, abs=1e-4)
    assert fact.numbers["indirect_pct"] == pytest.approx(3.5697, abs=1e-4)


def test_bara_book_exposure_spans_both_accounts(cl1_lookthrough):
    refs = {s.row_ref.split("|")[0]
            for s in cl1_lookthrough["F-CL0001-BOOKEXPOSURE"].sources}
    assert refs == {"PF-0001", "PF-0002"}


def test_cf0005_opens_in_breach_and_is_cured_by_the_market(cl1_collateral):
    """The mirror of CF-0001: cured by an event, not by an action."""
    breach = cl1_collateral["F-CL0001-COLLAT-BREACH"]
    assert breach.as_of == "2025-12-31"
    # 8,000,000 / 10,191,000 = 78.50063781768227; shipped column rounds to 78.5
    assert breach.numbers["ltv_pct"] == pytest.approx(78.5006, abs=1e-4)
    assert breach.numbers["trigger_pct"] == 70.0

    cure = cl1_collateral["F-CL0001-COLLAT-CURE"]
    assert MARKET_DRIVEN in cure.headline
    assert cure.numbers["drawn_change"] == 0.0        # nobody did anything
    assert cure.numbers["lending_change"] > 0


def test_the_wrapper_reconcentration_names_the_account_boundary(cl1_tension):
    """The sharpest finding: the note sits in the account he called safe."""
    fact = cl1_tension["F-CL0001-TENSION-WRAPPER-SYN-SP-0505"]
    assert fact.numbers["existing_exposure_pct"] == pytest.approx(44.9853, abs=1e-4)
    assert "PF-0002" in fact.detail and "PF-0001" in fact.detail


def test_source_of_wealth_tension_reports_the_matched_term(cl1_tension):
    fact = cl1_tension["F-CL0001-TENSION-SOURCEOFWEALTH"]
    assert "energy" in fact.detail
    assert fact.numbers["book_pct"] == pytest.approx(44.9853, abs=1e-4)


def test_tension_cites_the_rm_note_that_mentions_the_industry(cl1_tension):
    """N-002 records the coal conversation; the citation is derived, not chosen."""
    fact = cl1_tension["F-CL0001-TENSION-SOURCEOFWEALTH"]
    notes = {s.row_ref for s in fact.sources if s.file == "rm_notes.json"}
    assert "N-002" in notes


# --- tension matcher -------------------------------------------------------

def test_stem_matching_catches_the_pharma_case():
    from engine.analyzers.tension import shared_terms, significant_tokens
    wealth = significant_tokens("Executive compensation - pharmaceutical group board member")
    holding = significant_tokens("Kanto Pharma Holdings KK")
    assert shared_terms(wealth, holding) == {"pharmaceutical/pharma"}


def test_stem_matching_does_not_double_report_an_exact_match():
    from engine.analyzers.tension import shared_terms
    assert shared_terms({"industrial"}, {"industrial", "industrials"}) == {"industrial"}


def test_stem_matching_does_not_invent_a_link():
    from engine.analyzers.tension import shared_terms, significant_tokens
    wealth = significant_tokens("Entrepreneur - cross-border e-commerce platform")
    holding = significant_tokens("Helios Cloud Systems Inc")
    assert shared_terms(wealth, holding) == set()


# --- triage ----------------------------------------------------------------

def test_triage_weights_sum_to_100():
    from engine.analyzers.triage import load_config
    assert sum(load_config()["weights"].values()) == 100


def test_triage_ranks_every_client_with_holdings(ds):
    from engine.analyzers.triage import rank_book
    assert len(rank_book(ds)) == 20


def test_triage_is_ordered_by_score(ds):
    from engine.analyzers.triage import rank_book
    scores = [s.total for s in rank_book(ds)]
    assert scores == sorted(scores, reverse=True)


def test_triage_surfaces_the_tightest_facility_in_the_book(ds):
    """CL-0014 sits 0.59pp from its trigger and no deep analyzer looked at it."""
    from engine.analyzers.triage import rank_book
    top_three = [s.client_id for s in rank_book(ds)[:3]]
    assert "CL-0014" in top_three


# --- certainty must survive into the headline ------------------------------

@pytest.fixture(scope="module")
def cl2_liquidity(ds) -> dict[str, Fact]:
    from engine.analyzers import liquidity
    return {f.fact_id: f for f in liquidity.run(ds, ("CL-0002",))}


def test_a_conditional_need_says_so_in_its_headline(cl2_liquidity):
    """"Say this" renders headlines alone. A caveat one level down is a caveat
    the RM will state aloud as settled fact."""
    fact = cl2_liquidity["F-CL0002-LIQUIDITY-CN-002"]
    assert "conditional" in fact.headline.lower()
    assert "Conditional on the sale completing" in fact.headline


def test_a_likely_need_is_not_dressed_up_as_conditional(cl2_liquidity):
    fact = cl2_liquidity["F-CL0002-LIQUIDITY-CN-003"]
    assert "conditional" not in fact.headline.lower()
    assert "likely" in fact.headline.lower()


def test_conditional_severity_agrees_with_the_triage_discount(cl2_liquidity):
    """The ranking already discounts a conditional need. A fact calling it
    maximum severity contradicted the ranking that reads it."""
    conditional = cl2_liquidity["F-CL0002-LIQUIDITY-CN-002"]
    likely = cl2_liquidity["F-CL0002-LIQUIDITY-CN-003"]
    assert conditional.severity < likely.severity


def test_the_conditional_need_no_longer_leads_say_this(ds):
    """It is a real obligation, but not one of the three things to open with."""
    from engine.analyzers import liquidity, lookthrough, collateral, tension
    facts = (lookthrough.run(ds, ("CL-0002",)) + collateral.run(ds, ("CL-0002",))
             + tension.run(ds, ("CL-0002",)) + liquidity.run(ds, ("CL-0002",)))
    seen, top = set(), []
    for fact in sorted(facts, key=lambda f: -f.severity):
        if fact.severity < 60 or fact.kind in seen:
            continue
        seen.add(fact.kind)
        top.append(fact.fact_id)
        if len(top) == 3:
            break
    assert "F-CL0002-LIQUIDITY-CN-002" not in top
