"""The engine covers the whole book, and CL-0014's numbers are pinned.

Two separate claims live here.

The first is coverage: every client in the dataset goes through every analyzer.
Before this, eight analyzers ran over two hand-picked clients, so the console
could rank twenty people and open two of them. The README's best line - that the
book-level scan "found a client nobody was looking at" - pointed at a client
whose page did not exist.

The second is that the found client's figures are right. Every value below was
recomputed from data/ with the standard library's csv module before being
written here - not read off the engine's own output, and not off the dataset's
derived columns either. If one of these fails, the analyzer is wrong.

    CF-0002 LTV series        53.9306, 53.5300, 65.6179, 67.9552, 69.4063
    CF-0002 headroom now      0.5937 pp under a 70.00 trigger - tightest in book
    PF-0016 sellable          1,208,997.52 of 88,847,450.00 that looks tradeable
    PF-0016 Equity weight     23.3857% against a BAL band of 30-55%
    PF-0016 largest position  19.5767% against a BAL limit of 12%
"""

import pytest

from engine.analyzers import collateral, liquidity, mandate
from engine.build import DEEP_CLIENTS, clients_in, collect, selected_clients
from engine.loader import LATEST_SNAPSHOT, load
from engine.models import Fact

TIGHTEST = "CL-0014"


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def facts(ds) -> list[Fact]:
    return collect(ds, clients_in(ds))


def _by_id(produced) -> dict[str, Fact]:
    return {f.fact_id: f for f in produced}


# --- coverage ---------------------------------------------------------------

def test_the_build_covers_every_client_in_the_dataset(ds):
    assert selected_clients(ds) == clients_in(ds)
    assert len(selected_clients(ds)) == 20


def test_an_env_override_can_narrow_the_build(ds, monkeypatch):
    """A demo may want a smaller book; it may not invent one."""
    from engine.build import CLIENTS_ENV, UnknownClient

    monkeypatch.setenv(CLIENTS_ENV, "CL-0014, CL-0002")
    assert selected_clients(ds) == ("CL-0014", "CL-0002")

    monkeypatch.setenv(CLIENTS_ENV, "CL-9999")
    with pytest.raises(UnknownClient, match="CL-9999"):
        selected_clients(ds)


def test_every_client_gets_at_least_one_fact(ds, facts):
    """A client with no findings and a client never analysed look identical
    from the outside, which is the failure this guards."""
    covered = {f.client_id for f in facts}
    assert covered == set(clients_in(ds))


def test_the_deep_clients_are_a_subset_of_the_book(ds):
    assert set(DEEP_CLIENTS) <= set(clients_in(ds))


def test_the_tightest_facility_in_the_book_is_not_one_of_the_deep_clients():
    """The point of the book-level scan: it surfaced somebody we did not pick.

    If this ever fails because CL-0014 was promoted to a deep client, the claim
    in the README needs rewriting rather than this test relaxing.
    """
    assert TIGHTEST not in DEEP_CLIENTS


# --- GOLDEN: CF-0002, the tightest facility in the book ---------------------

def test_cf0002_ltv_series_recomputed_from_drawn_over_lending_value(ds):
    facility = ds.credit_facilities
    facility = facility[facility["facility_id"] == "CF-0002"].iloc[0]
    path = [point.ltv for point in collateral.ltv_path(facility)]
    assert path == pytest.approx(
        [53.9306, 53.5300, 65.6179, 67.9552, 69.4063], abs=1e-4
    )


def test_cf0002_is_0_59_points_from_a_margin_call(ds):
    fact = _by_id(collateral.run(ds, (TIGHTEST,)))["F-CL0014-COLLAT-CURRENT"]
    assert fact.numbers["ltv_pct"] == pytest.approx(69.4063, abs=1e-4)
    assert fact.numbers["trigger_pct"] == 70.0
    assert fact.numbers["distance_pp"] == pytest.approx(0.5937, abs=1e-4)


def test_cf0002_never_actually_breached(ds):
    """It is the tightest facility in the book and has never crossed its
    trigger. A breach fact here would be a false alarm on a real client."""
    produced = _by_id(collateral.run(ds, (TIGHTEST,)))
    assert "F-CL0014-COLLAT-BREACH" not in produced
    assert "F-CL0014-COLLAT-CURRENT" in produced


def test_cf0002_is_tighter_than_either_client_we_chose_to_study(ds):
    """The whole-book comparison, computed rather than asserted."""
    headroom: dict[str, float] = {}
    for _, facility in ds.credit_facilities.iterrows():
        drawn = float(facility[f"drawn_{LATEST_SNAPSHOT}"])
        lending = float(facility[f"lending_value_{LATEST_SNAPSHOT}"])
        trigger = float(facility["margin_call_ltv_pct"])
        headroom[str(facility["client_id"])] = trigger - drawn / lending * 100

    assert min(headroom, key=headroom.get) == TIGHTEST
    assert headroom[TIGHTEST] == pytest.approx(0.5937, abs=1e-4)
    for deep in DEEP_CLIENTS:
        if deep in headroom:
            assert headroom[TIGHTEST] < headroom[deep]


# --- GOLDEN: what can genuinely be sold out of PF-0016 ----------------------

def test_only_1_2m_of_pf0016_can_be_sold_before_cf0002_breaches(ds):
    fact = _by_id(liquidity.run(ds, (TIGHTEST,)))[
        "F-CL0014-LIQUIDITY-PLEDGED-CF-0002"
    ]
    assert fact.numbers["sellable"] == pytest.approx(1_208_997.52, abs=0.01)
    assert fact.numbers["apparent_daily"] == pytest.approx(88_847_450.00, abs=0.01)
    assert fact.numbers["advance_rate_pct"] == pytest.approx(58.6260, abs=1e-4)
    # The gap is the finding: under 2% of what a holdings report calls liquid.
    assert fact.numbers["sellable"] / fact.numbers["apparent_daily"] < 0.02


# --- GOLDEN: PF-0016 against its mandate ------------------------------------

def test_pf0016_equity_is_below_the_bal_band(ds):
    fact = _by_id(mandate.run(ds, (TIGHTEST,)))["F-CL0014-MANDATE-PF-0016-Equity"]
    assert fact.numbers["weight_pct"] == pytest.approx(23.3857, abs=1e-4)
    assert (fact.numbers["min_pct"], fact.numbers["max_pct"]) == (30.0, 55.0)
    assert fact.numbers["distance_pp"] == pytest.approx(30.0 - 23.3857, abs=1e-4)


def test_pf0016_largest_position_breaches_the_single_name_limit(ds):
    fact = _by_id(mandate.run(ds, (TIGHTEST,)))[
        "F-CL0014-MANDATE-SINGLEPOS-PF-0016-SYN-AL-0307"
    ]
    assert fact.numbers["weight_pct"] == pytest.approx(19.5767, abs=1e-4)
    assert fact.numbers["limit_pct"] == 12.0
    # Direct property, held directly - nothing reached through a wrapper here.
    assert fact.numbers["indirect_pct"] == pytest.approx(0.0, abs=1e-9)


# --- the invariants still hold at twenty clients ----------------------------

def test_every_fact_in_the_whole_book_carries_a_source(facts):
    assert all(f.sources for f in facts)


def test_no_fact_id_collides_across_the_whole_book(facts):
    ids = [f.fact_id for f in facts]
    assert len(ids) == len(set(ids))


def test_every_quote_in_the_whole_book_resolves_to_a_cited_row(ds, facts):
    """Quote verification exempts a quote's numerals from the claim check, so
    at twenty clients it has to hold for twenty clients."""
    from engine.build import source_rows
    from engine.sources import verify_quotes

    rows = source_rows(ds, facts)
    assert sum(len(verify_quotes(f, rows)) for f in facts) > 0
