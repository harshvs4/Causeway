"""Loader and FX convention.

The FX tests matter more than they look: market_context quotes each pair in its
own market convention, so a helper that divides the wrong way produces numbers
that are wrong but entirely plausible - the exact failure mode this product is
built to prevent.
"""

import pandas as pd
import pytest

from engine.loader import (
    LATEST_SNAPSHOT,
    SNAPSHOTS,
    Dataset,
    FxRates,
    UnknownCurrency,
    load,
)


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return load()


# --- loading ---------------------------------------------------------------

def test_every_file_loads_with_expected_grain(ds):
    assert len(ds.clients) == 20
    assert len(ds.portfolios) == 24
    assert len(ds.holdings) == 1015
    assert len(ds.instruments) == 62
    assert len(ds.mandates) == 48
    assert len(ds.transactions) == 393
    assert len(ds.credit_facilities) == 5
    assert len(ds.commitments) == 5
    assert len(ds.planned_cash_needs) == 20
    assert len(ds.market_context) == 115
    assert len(ds.event_log) == 16
    assert len(ds.rm_notes) == 28


def test_dates_are_coerced_not_left_as_strings(ds):
    assert pd.api.types.is_datetime64_any_dtype(ds.holdings["snapshot_date"])
    assert pd.api.types.is_datetime64_any_dtype(ds.event_log["event_date"])
    assert pd.api.types.is_datetime64_any_dtype(ds.rm_notes["note_date"])


def test_the_five_snapshots_are_what_we_think_they_are(ds):
    found = sorted(ds.holdings["snapshot_date"].dt.strftime("%Y-%m-%d").unique())
    assert tuple(found) == SNAPSHOTS


def test_holdings_at_filters_to_one_snapshot(ds):
    latest = ds.holdings_at(LATEST_SNAPSHOT)
    assert len(latest) < len(ds.holdings)
    assert latest["snapshot_date"].nunique() == 1


# --- FX convention ---------------------------------------------------------

def test_usd_is_identity(ds):
    assert ds.fx_at(LATEST_SNAPSHOT).usd_per("USD") == 1.0


def test_usd_quoted_pair_is_inverted(ds):
    """USDSGD is SGD per USD, so USD per SGD must be its reciprocal."""
    fx = ds.fx_at(LATEST_SNAPSHOT)
    row = ds.market_context[
        (ds.market_context["series_id"] == "USDSGD")
        & (ds.market_context["snapshot_date"] == pd.Timestamp(LATEST_SNAPSHOT))
    ]
    sgd_per_usd = float(row["value"].iloc[0])
    assert fx.usd_per("SGD") == pytest.approx(1.0 / sgd_per_usd)
    # sanity: a SGD is worth less than a USD, so the rate is below 1
    assert 0 < fx.usd_per("SGD") < 1


def test_usd_based_pair_is_taken_directly(ds):
    """EURUSD is already USD per EUR, so it must not be inverted."""
    fx = ds.fx_at(LATEST_SNAPSHOT)
    row = ds.market_context[
        (ds.market_context["series_id"] == "EURUSD")
        & (ds.market_context["snapshot_date"] == pd.Timestamp(LATEST_SNAPSHOT))
    ]
    usd_per_eur = float(row["value"].iloc[0])
    assert fx.usd_per("EUR") == pytest.approx(usd_per_eur)
    # sanity: a EUR is worth more than a USD
    assert fx.usd_per("EUR") > 1


def test_conversion_round_trips(ds):
    fx = ds.fx_at(LATEST_SNAPSHOT)
    sgd = 1_000_000.0
    assert fx.convert(fx.convert(sgd, "SGD", "USD"), "USD", "SGD") == pytest.approx(sgd)


def test_same_currency_conversion_is_a_noop(ds):
    assert ds.fx_at(LATEST_SNAPSHOT).convert(123.45, "SGD", "SGD") == 123.45


def test_unknown_currency_raises_rather_than_defaulting(ds):
    with pytest.raises(UnknownCurrency):
        ds.fx_at(LATEST_SNAPSHOT).usd_per("ZWL")


def test_every_snapshot_has_rates_for_every_portfolio_currency(ds):
    needed = set(ds.portfolios["base_currency"].unique())
    for snapshot in SNAPSHOTS:
        fx = ds.fx_at(snapshot)
        for currency in needed:
            assert fx.usd_per(currency) > 0


def test_unknown_snapshot_raises(ds):
    with pytest.raises(KeyError):
        ds.fx_at("2026-01-01")


def test_fx_rates_ignores_non_fx_series(ds):
    fx = ds.fx_at(LATEST_SNAPSHOT)
    # GOLD_USD_OZ is a commodity series, not a currency pair
    assert "GOL" not in fx.rates
