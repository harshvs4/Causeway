"""The guardrails, tested as guardrails.

Two mechanisms here exist to stop a wrong number reaching a client
conversation, and both have already caught real defects during the build:

  - quote verification, which closes the hole in the numeral check
  - duplicate fact_id refusal, which caught two mandate facts silently
    overwriting each other

Neither is useful unless it stays enforced, so both are pinned here.
"""

import pytest
from pydantic import ValidationError

from engine.analyzers import tension
from engine.build import DuplicateFactId, collect, source_rows
from engine.loader import load
from engine.models import Fact, Source
from engine.sources import QuoteNotInSource, verify_quotes


@pytest.fixture(scope="module")
def ds():
    return load()


# --- quotes must exist in the data, not merely in the fact -----------------

REAL_SOURCE = Source(
    file="clients.csv", row_ref="CL-0001", fields=("source_of_wealth",)
)


def _fact(**overrides) -> Fact:
    kwargs = dict(
        fact_id="F-TEST-QUOTE",
        client_id="CL-0001",
        kind="tension",
        headline="Source of wealth is recorded as \"Inherited - family coal mining and energy group\".",
        detail="",
        numbers={},
        quotes=("Inherited - family coal mining and energy group",),
        sources=(REAL_SOURCE,),
        as_of="2026-08-26",
        confidence="derived",
        severity=50,
    )
    kwargs.update(overrides)
    return Fact(**kwargs)


def test_a_genuine_quote_resolves_to_its_source_field(ds):
    fact = _fact()
    rows = source_rows(ds, [fact])
    resolved = verify_quotes(fact, rows)
    assert resolved["Inherited - family coal mining and energy group"] == (
        "clients.csv",
        "source_of_wealth",
    )


def test_a_fabricated_quote_is_refused(ds):
    """The hole this closes: an invented quote laundering invented numbers."""
    fact = _fact(
        headline="Source of wealth is recorded as \"Inherited - family gold mining empire worth 900\".",
        quotes=("Inherited - family gold mining empire worth 900",),
    )
    rows = source_rows(ds, [fact])
    with pytest.raises(QuoteNotInSource, match="appears in none of the cited rows"):
        verify_quotes(fact, rows)


def test_a_quote_from_a_row_the_fact_does_not_cite_is_refused(ds):
    """Real text, wrong provenance. Still a fabrication as far as this fact goes."""
    fact = _fact(
        headline="Life stage is \"Pre-liquidity event\".",
        quotes=("Pre-liquidity event",),          # true of CL-0002, not cited here
    )
    rows = source_rows(ds, [fact])
    with pytest.raises(QuoteNotInSource):
        verify_quotes(fact, rows)


def test_an_empty_quote_is_refused(ds):
    fact = _fact(headline="Nothing claimed here.", quotes=("",))
    rows = source_rows(ds, [fact])
    with pytest.raises(QuoteNotInSource, match="empty quote"):
        verify_quotes(fact, rows)


def test_every_shipped_quote_survives_verification(ds):
    """The whole build, not a fixture: no analyzer may quote what it cannot cite."""
    facts = collect(ds)
    rows = source_rows(ds, facts)
    total = sum(len(verify_quotes(f, rows)) for f in facts)
    assert total > 0, "no quotes exercised - the check would be vacuous"


def test_quotes_still_exempt_only_their_own_numerals():
    """A number outside a declared quote remains a claim."""
    with pytest.raises(ValidationError, match="no value in numbers renders to it"):
        _fact(
            headline=(
                "Source of wealth is recorded as \"Inherited - family coal mining "
                "and energy group\" and it is 44.99% of the book."
            ),
            numbers={},
        )


# --- duplicate fact ids -----------------------------------------------------

def test_duplicate_fact_ids_are_refused(monkeypatch, ds):
    """Caught a real collision: two mandate facts overwriting each other."""
    from engine import build as build_module

    twin = _fact()
    monkeypatch.setattr(
        build_module, "ANALYZERS", [("twins", lambda _ds: [twin, twin])]
    )
    with pytest.raises(DuplicateFactId, match="F-TEST-QUOTE"):
        build_module.collect(ds)


def test_distinct_fact_ids_pass(monkeypatch, ds):
    from engine import build as build_module

    monkeypatch.setattr(
        build_module,
        "ANALYZERS",
        [("pair", lambda _ds: [_fact(), _fact(fact_id="F-TEST-QUOTE-2")])],
    )
    assert len(build_module.collect(ds)) == 2


def test_the_real_build_has_no_duplicate_ids(ds):
    facts = collect(ds)
    ids = [f.fact_id for f in facts]
    assert len(ids) == len(set(ids))


# --- stem matching must not accept coincidence ------------------------------

def test_market_descriptors_cannot_stem_match_an_industry():
    """'property development' must not match 'Global Developed Equity Index Fund'."""
    wealth = tension.significant_tokens("Entrepreneur - property development")
    holding = tension.significant_tokens("Global Developed Equity Index Fund")
    assert tension.shared_terms(wealth, holding) == set()


def test_only_one_stem_pair_exists_across_the_whole_book(ds):
    """A firing rate is only trustworthy if the matcher is not loose."""
    instruments = ds.instruments.set_index("instrument_id")
    holdings = ds.holdings_at("2026-08-26")
    pairs: set[str] = set()
    for client_id in ds.clients["client_id"]:
        wealth = tension.significant_tokens(
            ds.clients.set_index("client_id").loc[client_id, "source_of_wealth"]
        )
        other: set[str] = set()
        for _, row in holdings[holdings["client_id"] == client_id].iterrows():
            other |= tension.significant_tokens(row["instrument_name"])
            if row["instrument_id"] in instruments.index:
                other |= tension.significant_tokens(
                    instruments.loc[row["instrument_id"], "sector"]
                )
        pairs |= {p for p in tension.shared_terms(wealth, other) if "/" in p}
    assert pairs == {"pharmaceutical/pharma"}
