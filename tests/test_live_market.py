"""The live feed, the sensitivity map, and the marks they produce.

NOTHING HERE TOUCHES THE NETWORK. Every live case runs against `FakeQuotes`, a
hand-built source whose figures are chosen so the arithmetic can be checked by
hand. A test that reached for a real quote would fail on a public holiday, pass
for the wrong reason on a quiet day, and never say which.

The arithmetic pinned below was worked out by hand, not read off the code:

    tech equity      NASDAQ -2.00%  x beta 1.15        = -2.30%
    tech-linked ELN  NASDAQ -2.00%  x beta 0.70        = -1.40%
    government bond  10y 4.986 -> 4.929 = -0.057 pts
                     x duration 7.0, sign flipped      = +0.399%
    direct property  rule is `none`                    =  0.000%

The sign flip is worth stating twice. A falling yield is a rising bond price,
and treating a yield like a price produces a wrong number small enough to look
plausible.
"""

import pytest

from engine.analyzers import live_mark
from engine.live_feed import (
    SOURCE_ENV,
    FeedConfigError,
    Quote,
    SnapshotQuotes,
    fetch_all,
    load_config,
    quote_source,
)
from engine.loader import LATEST_SNAPSHOT, load
from engine.marks import (
    SensitivityConfigError,
    derive_marks,
    load_sensitivities,
    match_rule,
    revalue_holdings,
)
from engine.sources import resolve

MATCHABLE = ("asset_class", "sub_asset_class", "sector", "region")


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def feed():
    return load_config()


@pytest.fixture(scope="module")
def sens():
    return load_sensitivities()


class FakeQuotes:
    """A controlled market. Values chosen to make the arithmetic checkable."""

    MOVES = {
        # series_id: (value, previous_close)
        "NASDAQ_COMP": (24_500.0, 25_000.0),      # exactly -2.00%
        "SPX": (5_940.0, 6_000.0),                # exactly -1.00%
        "HSI": (20_200.0, 20_000.0),              # exactly +1.00%
        "STI": (3_030.0, 3_000.0),                # exactly +1.00%
        "MSCI_ASIA_XJP": (99.0, 100.0),           # exactly -1.00%
        "UST_10Y_PCT": (4.929, 4.986),            # -0.057 points
        "GOLD_USD_OZ": (3_030.0, 3_000.0),        # exactly +1.00%
        "BRENT_USD_BBL": (79.2, 80.0),            # exactly -1.00%
        "VIX": (22.0, 20.0),
    }

    def fetch(self, series_ids):
        out = {}
        for series_id in series_ids:
            if series_id not in self.MOVES:
                continue
            value, previous = self.MOVES[series_id]
            out[series_id] = Quote(
                series_id=series_id,
                value=value,
                previous_close=previous,
                as_of="2026-11-01T12:00:00+00:00",
                origin=f"fake:{series_id}",
                live=True,
            )
        return out


@pytest.fixture(scope="module")
def live(ds, sens):
    quotes = FakeQuotes().fetch(list(FakeQuotes.MOVES))
    return quotes, derive_marks(ds, quotes, sens)


# --- the configuration has to be arguable, which means complete -------------

def test_every_proxy_says_why_it_is_a_stand_in(feed):
    """A substitution whose reasoning is not written down is not auditable."""
    for series_id, spec in feed["series"].items():
        if spec.get("proxy"):
            assert str(spec.get("why", "")).strip(), series_id


def test_every_gap_says_why_there_is_no_quote(feed):
    for series_id, reason in feed["gaps"].items():
        assert str(reason).strip(), series_id


def test_a_series_cannot_be_both_quotable_and_a_gap(tmp_path, feed):
    import yaml

    broken = dict(feed)
    broken["gaps"] = {**feed["gaps"], "SPX": "contradiction"}
    path = tmp_path / "market_feed.yaml"
    path.write_text(yaml.safe_dump(broken))
    with pytest.raises(FeedConfigError, match="both quotable and a gap"):
        load_config(path)


def test_every_sensitivity_rule_states_its_reasoning(sens):
    for rule in sens["rules"]:
        assert str(rule.get("why", "")).strip(), rule["id"]


def test_a_rule_without_reasoning_is_refused(tmp_path, sens):
    import yaml

    broken = {
        "defaults": sens["defaults"],
        "rules": [
            {"id": "naked", "match": {"asset_class": "Equity"},
             "mode": "relative", "factor": "SPX", "beta": 1.0}
        ],
    }
    path = tmp_path / "market_sensitivities.yaml"
    path.write_text(yaml.safe_dump(broken))
    with pytest.raises(SensitivityConfigError, match="needs a 'why'"):
        load_sensitivities(path)


def test_every_instrument_matches_a_real_rule(ds, sens):
    """Falling through to the default means an instrument silently stops
    responding to the market, which is worse than a debatable beta."""
    unmatched = [
        str(row["instrument_id"])
        for _, row in ds.instruments.iterrows()
        if match_rule({f: row.get(f) for f in MATCHABLE}, sens)["id"] == "default"
    ]
    assert unmatched == []


def test_every_factor_named_by_a_rule_is_a_series_the_feed_knows(feed, sens):
    known = set(feed["series"]) | set(feed.get("gaps", {}))
    for rule in sens["rules"]:
        if rule.get("factor"):
            assert rule["factor"] in known, rule["id"]


# --- the default must stay reproducible -------------------------------------

def test_the_default_price_source_is_the_snapshot(monkeypatch):
    monkeypatch.delenv(SOURCE_ENV, raising=False)
    assert isinstance(quote_source(), SnapshotQuotes)


def test_an_unknown_price_source_is_refused(monkeypatch):
    monkeypatch.setenv(SOURCE_ENV, "whatever")
    with pytest.raises(ValueError, match="expected 'snapshot' or 'live'"):
        quote_source()


def test_the_snapshot_source_moves_nothing(ds, sens, feed):
    """The goldens elsewhere are pinned to four decimals. If a default build
    could move a price, every one of them becomes a function of the tape."""
    quotes = fetch_all(SnapshotQuotes(), feed)
    marks = derive_marks(ds, quotes, sens)
    assert marks
    assert [m.instrument_id for m in marks.values() if m.moved] == []


def test_a_snapshot_quote_is_never_differenced_against_itself(ds, sens, feed):
    """The trap this avoids: the dataset's SPX is synthetic and today's is real.
    Differencing them would report fiction-versus-reality as a market move."""
    quotes = fetch_all(SnapshotQuotes(), feed)
    assert all(q.previous_close is None for q in quotes.values())
    assert all(q.pct_change is None for q in quotes.values())


def test_a_quote_that_is_not_live_cannot_move_a_mark(ds, sens):
    stale = {
        "NASDAQ_COMP": Quote(
            series_id="NASDAQ_COMP", value=24_500.0, previous_close=25_000.0,
            as_of=LATEST_SNAPSHOT, origin="market_context.csv", live=False,
        )
    }
    marks = derive_marks(ds, stale, sens)
    assert marks["SYN-ST-0103"].price_pct == 0.0
    assert marks["SYN-ST-0103"].price == marks["SYN-ST-0103"].base_price


# --- GOLDEN: the arithmetic, checked by hand --------------------------------

def test_tech_equity_scales_with_the_nasdaq_at_its_published_beta(live):
    _, marks = live
    mark = marks["SYN-ST-0103"]             # Helios Cloud Systems Inc
    assert mark.rule_id == "equity.tech.north_america"
    assert mark.mode == "relative"
    assert mark.factor == "NASDAQ_COMP"
    assert mark.coefficient == pytest.approx(1.15)
    assert mark.factor_move == pytest.approx(-2.0, abs=1e-9)
    assert mark.price_pct == pytest.approx(-2.30, abs=1e-9)


def test_the_wrapper_on_that_name_participates_less_than_the_name(live):
    """An equity-linked note is damped by construction. If the note ever moved
    as much as its underlying, the wrapper would be doing nothing."""
    _, marks = live
    note = marks["SYN-SP-0502"]             # ELN referencing Helios
    assert note.rule_id == "structured.tech.north_america"
    assert note.coefficient == pytest.approx(0.70)
    assert note.price_pct == pytest.approx(-1.40, abs=1e-9)
    assert abs(note.price_pct) < abs(marks["SYN-ST-0103"].price_pct)


def test_a_falling_yield_raises_a_bond_price(live):
    """Duration mode, and the sign flip. A yield series moving 4.986 -> 4.929 is
    -0.057 POINTS; at seven years of duration that is +0.399% on the price."""
    _, marks = live
    bond = marks["SYN-FI-0201"]             # US Treasury 2.375% due 2045
    assert bond.mode == "duration"
    assert bond.factor == "UST_10Y_PCT"
    assert bond.coefficient == pytest.approx(7.0)
    assert bond.factor_move == pytest.approx(-0.057, abs=1e-9)
    assert bond.price_pct == pytest.approx(0.399, abs=1e-9)
    assert bond.price > bond.base_price


def test_appraisal_valued_assets_do_not_reprice_on_an_index(live):
    """Direct property and a call deposit are the two things that must not move.
    A daily mark on either would be fabricated."""
    _, marks = live
    for instrument_id in ("SYN-AL-0307", "SYN-CA-0603"):
        mark = marks[instrument_id]
        assert mark.mode == "none"
        assert mark.price_pct == 0.0
        assert mark.price == mark.base_price


def test_revaluing_holds_quantity_fixed_and_moves_only_price(ds, live):
    """Nobody traded. A re-mark must not look like a transaction."""
    _, marks = live
    before = ds.holdings_at(LATEST_SNAPSHOT)
    total_before = float(before["market_value_base"].sum())
    after = revalue_holdings(ds, marks)

    assert len(before) == len(after)
    if "quantity" in before.columns:
        assert (before["quantity"].values == after["quantity"].values).all()
    # and the loaded dataset is not mutated by having been re-marked
    assert float(
        ds.holdings_at(LATEST_SNAPSHOT)["market_value_base"].sum()
    ) == pytest.approx(total_before)


# --- the fact a live mark produces -----------------------------------------

@pytest.fixture(scope="module")
def live_facts(ds, live):
    quotes, marks = live
    return live_mark.run(ds, tuple(ds.clients["client_id"]), quotes, marks)


def test_no_live_source_means_no_live_facts(ds):
    """The default build path. Returns nothing rather than guessing."""
    assert live_mark.run(ds, ("CL-0002",), None, None) == []
    assert live_mark.run(ds, ("CL-0002",), {}, {}) == []


def test_the_pledged_facility_is_remarked(ds, live_facts):
    fact = {f.fact_id: f for f in live_facts}["F-CL0002-LIVEMARK-CF-0001"]
    # Ravi's collateral is mostly US tech, so a NASDAQ fall raises his LTV.
    assert fact.numbers["ltv_pct"] > fact.numbers["ltv_on_statement_pct"]
    assert fact.numbers["shift_pp"] > 0
    assert fact.numbers["trigger_pct"] == 75.0
    assert fact.numbers["distance_pp"] == pytest.approx(
        75.0 - fact.numbers["ltv_pct"], abs=1e-9
    )
    # the arithmetic ties back to the facility row it cites
    assert fact.numbers["ltv_pct"] == pytest.approx(
        fact.numbers["drawn"] / fact.numbers["lending_value_marked"] * 100,
        abs=1e-9,
    )


def test_a_live_fact_cites_the_quote_and_the_rule_not_just_the_csv(
    ds, live, live_facts
):
    """The governance claim applied to a live number: an estimate has to arrive
    with the quote it came from and the sensitivity that shaped it."""
    quotes, marks = live
    extra = live_mark.extra_source_rows(quotes, marks)
    fact = {f.fact_id: f for f in live_facts}["F-CL0002-LIVEMARK-CF-0001"]

    files = {s.file for s in fact.sources}
    assert "market_data" in files
    assert "market_sensitivities.yaml" in files
    assert "credit_facilities.csv" in files

    for source in fact.sources:
        row = resolve(ds, source.file, source.row_ref, extra)
        assert row, f"{source.file}::{source.row_ref} resolved to nothing"

    quoted = next(s for s in fact.sources if s.file == "market_data")
    row = resolve(ds, quoted.file, quoted.row_ref, extra)
    assert row["live"] is True
    assert row["pct_change"] is not None
    assert row["as_of"]                     # a live number without a time is unusable


def test_the_rules_reasoning_is_quoted_from_the_rule_itself(ds, live, live_facts):
    """A beta justified by invented prose would be the obvious way around the
    guardrail. The quote has to resolve to a real field of a cited row."""
    from engine.sources import verify_quotes

    quotes, marks = live
    extra = live_mark.extra_source_rows(quotes, marks)
    rows: dict[str, dict] = {}
    for fact in live_facts:
        for source in fact.sources:
            key = f"{source.file}::{source.row_ref}"
            rows.setdefault(key, resolve(ds, source.file, source.row_ref, extra))

    resolved = verify_quotes(
        {f.fact_id: f for f in live_facts}["F-CL0002-LIVEMARK-CF-0001"], rows
    )
    assert resolved
    assert all(file == "market_sensitivities.yaml" for file, _ in resolved.values())


def test_a_live_fact_is_never_filed_as_a_hypothesis(live_facts):
    """Scenario/ is for things that have not happened. A mark on today's market
    is derived, not forward-looking, and must not land in that folder."""
    assert live_facts
    for fact in live_facts:
        assert fact.confidence == "derived"
        assert fact.kind != "scenario"


def test_the_fact_says_these_are_marks_rather_than_prices(live_facts):
    for fact in live_facts:
        assert "derived marks, not traded prices" in fact.detail
