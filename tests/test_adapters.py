"""Attribution and Scenario reach the product only as Facts.

Both computations were written outside the Fact contract and merged in. These
tests pin the thing that makes that merge safe: their output goes through the
same two invariants as everything else, and a hypothesis can never be mistaken
for the record - not in a folder, not in a cue, and not in a spoken answer.
"""

import asyncio

import pytest

from engine.analyzers import attribution, scenario
from engine.loader import load
from engine.models import Fact


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def attribution_facts(ds) -> list[Fact]:
    return attribution.run(ds, ("CL-0002", "CL-0001"))


@pytest.fixture(scope="module")
def scenario_facts(ds) -> list[Fact]:
    return scenario.run(ds, ("CL-0002", "CL-0001"))


# --- attribution -----------------------------------------------------------

def test_attribution_produces_facts(attribution_facts):
    assert attribution_facts


def test_every_attribution_cites_both_ends_of_the_window(attribution_facts):
    """A change is a claim about two dates, so both rows are cited."""
    for fact in attribution_facts:
        snapshots = {
            s.row_ref.split("|")[2]
            for s in fact.sources
            if s.file == "holdings.csv"
        }
        assert len(snapshots) == 2, f"{fact.fact_id} cites {snapshots}"


def test_every_attribution_cites_the_event_it_blames(attribution_facts):
    for fact in attribution_facts:
        assert any(s.file == "event_log.csv" for s in fact.sources)


def test_attribution_states_the_link_is_keyword_overlap_not_causation(attribution_facts):
    """The honest framing: we matched transmission channels, we did not prove
    that the event caused the move."""
    for fact in attribution_facts:
        assert "not" in fact.detail and "causation" in fact.detail


def test_attribution_ignores_moves_too_small_to_explain(attribution_facts):
    for fact in attribution_facts:
        assert fact.numbers["delta_pct"] >= attribution.MIN_ABS_PCT


def test_attribution_ignores_coincidences_of_date(attribution_facts):
    for fact in attribution_facts:
        assert fact.numbers["confidence"] >= attribution.MIN_CONFIDENCE


# --- scenario --------------------------------------------------------------

def test_scenario_produces_facts_for_both_directions(scenario_facts):
    names = {f.fact_id.rsplit("-", 1)[-1] for f in scenario_facts}
    assert names == {"ESCALATE", "REOPEN"}


def test_every_scenario_fact_is_marked_as_one(scenario_facts):
    for fact in scenario_facts:
        assert fact.kind == "scenario"
        assert fact.confidence == "scenario"
        assert fact.is_verified is False


def test_every_scenario_fact_says_it_has_not_happened(scenario_facts):
    """The folder and the styling both matter, but neither replaces the sentence."""
    for fact in scenario_facts:
        assert "has not happened" in fact.detail


def test_scenario_assumptions_are_declared_not_smuggled(scenario_facts):
    """The label carries a Brent level. It is our text, not a quotation, so its
    numbers are declared rather than exempted as a quote."""
    for fact in scenario_facts:
        assert "assumed_brent_usd" in fact.numbers
        assert fact.numbers["assumed_brent_usd"] in (75.0, 100.0)


def test_scenario_anchors_itself_to_real_dated_events(scenario_facts):
    for fact in scenario_facts:
        events = {s.row_ref for s in fact.sources if s.file == "event_log.csv"}
        assert events, f"{fact.fact_id} cites no event"


def test_the_energy_book_moves_opposite_ways_in_the_two_cases(scenario_facts):
    """CL-0001 is a bet on the Strait staying shut. If the model said otherwise
    the shock rules would be wrong."""
    by_id = {f.fact_id: f for f in scenario_facts}
    escalate = by_id["F-CL0001-SCENARIO-HORMUZ-ESCALATE"]
    reopen = by_id["F-CL0001-SCENARIO-HORMUZ-REOPEN"]
    assert "gains" in escalate.headline
    assert "loses" in reopen.headline


# --- the boundary ----------------------------------------------------------

def test_a_scenario_fact_cannot_claim_to_be_settled():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Fact(
            fact_id="F-TEST", client_id="CL-0002", kind="scenario",
            headline="Brent to 140.", numbers={"brent": 140.0},
            sources=(attribution.Source(file="clients.csv", row_ref="CL-0002",
                                        fields=("client_id",)),),
            as_of="2026-08-26", confidence="derived", severity=10,
        )


def test_the_vault_routes_a_hypothesis_away_from_the_record():
    from vault_build import build_vault
    vault = build_vault()
    scenario_notes = {p.stem for p in (vault / "Scenario").glob("*.md")}
    verified_notes = {p.stem for p in (vault / "Verified").glob("*.md")}
    assert any("SCENARIO" in n for n in scenario_notes)
    assert not any("SCENARIO" in n for n in verified_notes)


def test_scenario_never_reaches_a_live_cue():
    """The bug this pins: a question about what is true was being answered with
    a forward-looking hypothesis, as flat text with nothing marking it."""
    from assist.app import _indexes, load_facts
    load_facts()
    for index in _indexes.values():
        assert all(f["kind"] != "scenario" for f in index.facts)


def test_a_spoken_answer_never_contains_a_hypothesis():
    from assist.app import _indexes, load_facts, MIN_RELEVANCE, RELATIVE_CUTOFF
    load_facts()
    index = _indexes["CL-0001"]
    for question in ["what if the strait reopens", "what happens if hormuz escalates",
                     "is his collateral under pressure"]:
        hits = index.search(question, limit=3, min_score=MIN_RELEVANCE,
                            relative_cutoff=RELATIVE_CUTOFF)
        assert all("SCENARIO" not in h.fact_id for h in hits), question
