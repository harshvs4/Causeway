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


# ===========================================================================
# What the agent is actually handed, versus what the engine computed
# ===========================================================================

def _envelope():
    return json.loads((BUILD_DIR / "facts.json").read_text())


def test_utilisation_is_not_a_computed_fact_number():
    """72.22% appears in no Fact's numbers - the engine never derives it."""
    from engine.grounding import grounded_values as strict
    values = strict(_envelope()["facts"], "CL-0002")
    assert not is_supported(72.22, 2, values)


def test_utilisation_is_in_the_row_get_fact_hands_over():
    """But it is a real column on the facility the fact cites."""
    envelope = _envelope()
    row = envelope["source_rows"]["credit_facilities.csv::CF-0001"]
    assert float(row["utilisation_pct_current"]) == pytest.approx(72.22)
    # and it is a different measure from LTV, against a different denominator
    assert float(row["drawn_2026-08-26"]) / float(row["credit_limit"]) * 100 == \
        pytest.approx(72.22, abs=0.01)
    assert float(row["drawn_2026-08-26"]) / float(row["lending_value_2026-08-26"]) * 100 == \
        pytest.approx(73.71, abs=0.01)


def test_retrievable_values_accepts_what_the_agent_could_have_read():
    from engine.grounding import retrievable_values
    values = retrievable_values(_envelope(), "CL-0002")
    assert is_supported(72.22, 2, values)


def test_the_spoken_transcript_is_clean_against_the_right_set():
    """The line from the live Test Audio run. Honest retrieval, not fabrication."""
    from engine.grounding import retrievable_values
    said = ("The current utilisation of the facility is 72.22%, the LTV is "
            "73.71%, and the drawn amount is 6,500,000.")
    assert ungrounded(said, retrievable_values(_envelope(), "CL-0002")) == []


@pytest.mark.parametrize("said, bad", [
    ("My Helios weighting is 31.20%.", "31.20"),
    ("The stake is worth USD 44,500,000.", "44,500,000"),
    ("Utilisation is 61.50%.", "61.50"),
    ("My LTV is 88.40%.", "88.40"),
])
def test_widening_does_not_let_fabrications_through(said, bad):
    """The looser set must still catch a number nothing in the data supports."""
    from engine.grounding import retrievable_values
    assert bad in ungrounded(said, retrievable_values(_envelope(), "CL-0002"))


def test_retrievable_is_scoped_to_rows_actually_cited():
    """A number from a row no fact cites is still unsupported."""
    from engine.grounding import retrievable_values
    envelope = _envelope()
    values = retrievable_values(envelope, "CL-0002")
    cited = {f"{s['file']}::{s['row_ref']}"
             for f in envelope["facts"] if f["client_id"] == "CL-0002"
             for s in f["sources"]}
    assert cited, "no citations to scope to"
    assert len(values) < sum(len(r) for r in envelope["source_rows"].values())


# ===========================================================================
# Provenance: a pass has to be evidence, not an assertion
# ===========================================================================

def test_a_grounded_number_names_the_row_and_field_that_supports_it():
    from engine.grounding import explain, retrievable_sources
    claims = explain("utilisation is 72.22%", retrievable_sources(_envelope(), "CL-0002"))
    assert len(claims) == 1
    assert claims[0].grounded
    assert claims[0].supported_by.origin == \
        "credit_facilities.csv::CF-0001.utilisation_pct_current"
    assert str(claims[0]) == \
        "72.22 matches credit_facilities.csv::CF-0001.utilisation_pct_current"


def test_a_computed_number_is_attributed_to_the_fact_that_produced_it():
    from engine.grounding import explain, grounded_sources
    claims = explain("the LTV is 75.64%", grounded_sources(_envelope()["facts"], "CL-0002"))
    assert claims[0].grounded
    assert claims[0].supported_by.origin.startswith("F-CL0002-COLLAT")
    assert claims[0].supported_by.origin.endswith(".ltv_pct")


def test_an_ungrounded_number_says_so_and_names_nothing():
    from engine.grounding import explain, retrievable_sources
    claims = explain("weighting is 31.20%", retrievable_sources(_envelope(), "CL-0002"))
    assert not claims[0].grounded
    assert claims[0].supported_by is None
    assert str(claims[0]) == "31.20 — UNGROUNDED"


def test_provenance_survives_a_restatement():
    """13.2 is 13,207,200 said aloud; the citation must still be exact."""
    from engine.grounding import explain, retrievable_sources
    claims = explain("about 13.2 million looks liquid",
                     retrievable_sources(_envelope(), "CL-0002"))
    assert claims[0].grounded
    assert "F-CL0002-LIQUIDITY" in claims[0].supported_by.origin


def test_trailing_punctuation_is_not_part_of_the_number():
    from engine.grounding import claimed
    assert [w for w, _, _ in claimed("drawn is 6,500,000, and that is that")] \
        == ["6,500,000"]


def test_explain_and_ungrounded_agree():
    from engine.grounding import explain, retrievable_sources, ungrounded
    sources = retrievable_sources(_envelope(), "CL-0002")
    said = "72.22% utilisation, 31.20% weighting, 44,500,000 stake"
    assert ungrounded(said, sources) == \
        [c.written for c in explain(said, sources) if not c.grounded]


def test_stemming_is_symmetric_between_query_and_document():
    """Over-stemming is only safe if both sides are stemmed the same way."""
    from engine.retrieval import stem, tokenise
    assert stem("breached") == stem("breach")
    assert stem("warned") == stem("warn")
    assert tokenise("did he breach") == tokenise("has he breached")


def test_stemming_does_not_collapse_unrelated_words():
    from engine.retrieval import stem
    assert stem("collateral") != stem("collect")
    assert stem("trigger") != stem("trade")
    assert stem("market") != stem("margin")
