"""Typed loading of the read-only Julius Baer dataset, plus FX conventions.

`data/` is never written to. Every loader returns a fresh frame.

The FX helper is deliberately the only place in the codebase permitted to touch
an exchange rate. market_context.csv quotes each pair in *market* convention -
USDSGD is SGD per USD, EURUSD is USD per EUR - so dividing the wrong way is the
single easiest route to a confidently wrong number, which is precisely the
failure this product exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
BUILD_DIR = REPO_ROOT / "build"

SNAPSHOTS: tuple[str, ...] = (
    "2025-12-31",
    "2026-02-27",
    "2026-03-31",
    "2026-06-30",
    "2026-08-26",
)
LATEST_SNAPSHOT = SNAPSHOTS[-1]

# Columns that are dates wherever they appear.
_DATE_COLUMNS = {
    "snapshot_date",
    "valuation_date",
    "acquired_date",
    "inception_date",
    "event_date",
    "trade_date",
    "settlement_date",
    "note_date",
    "client_since",
    "kyc_review_due",
    "due_from",
    "due_to",
}


class UnknownCurrency(KeyError):
    """No rate available. Raised rather than defaulting, which would be worse."""


def _read_csv(name: str) -> pd.DataFrame:
    frame = pd.read_csv(DATA_DIR / name)
    for column in frame.columns:
        if column in _DATE_COLUMNS:
            frame[column] = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
    return frame


@dataclass(frozen=True)
class FxRates:
    """USD-per-unit rates for a single snapshot.

    A market_context FX series id is <base><quote> and its value is quote-per-
    base. USDSGD = 1.34 means 1.34 SGD per USD, so USD-per-SGD is 1/1.34.
    GBPUSD = 1.27 means 1.27 USD per GBP, so USD-per-GBP is 1.27 directly.
    """

    snapshot: str
    rates: dict[str, float]  # currency -> USD per 1 unit of that currency

    def usd_per(self, currency: str) -> float:
        code = currency.upper()
        if code == "USD":
            return 1.0
        try:
            return self.rates[code]
        except KeyError as exc:
            raise UnknownCurrency(
                f"no USD rate for {code!r} at {self.snapshot}; "
                f"known: {sorted(self.rates)}"
            ) from exc

    def convert(self, amount: float, frm: str, to: str) -> float:
        """Convert between any two currencies with a rate at this snapshot."""
        if frm.upper() == to.upper():
            return float(amount)
        return float(amount) * self.usd_per(frm) / self.usd_per(to)

    def to_usd(self, amount: float, frm: str) -> float:
        return self.convert(amount, frm, "USD")

    @classmethod
    def from_market_context(cls, market_context: pd.DataFrame, snapshot: str) -> "FxRates":
        rows = market_context[
            (market_context["snapshot_date"] == pd.Timestamp(snapshot))
            & (market_context["category"] == "FX")
        ]
        rates: dict[str, float] = {}
        for series_id, value in zip(rows["series_id"], rows["value"], strict=True):
            code = str(series_id).strip().upper()
            if len(code) != 6:
                continue
            base, quote = code[:3], code[3:]
            value = float(value)
            if value <= 0:
                continue
            if base == "USD":
                rates[quote] = 1.0 / value      # value is quote per USD
            elif quote == "USD":
                rates[base] = value             # value is USD per base
            # crosses (neither leg USD) are ignored; the dataset has none
        return cls(snapshot=snapshot, rates=rates)


@dataclass(frozen=True)
class Dataset:
    """Every input, loaded once, typed, read-only."""

    clients: pd.DataFrame
    portfolios: pd.DataFrame
    holdings: pd.DataFrame
    instruments: pd.DataFrame
    mandates: pd.DataFrame
    transactions: pd.DataFrame
    credit_facilities: pd.DataFrame
    commitments: pd.DataFrame
    planned_cash_needs: pd.DataFrame
    market_context: pd.DataFrame
    event_log: pd.DataFrame
    rm_notes: pd.DataFrame

    @cached_property
    def fx(self) -> dict[str, FxRates]:
        """FX rates per snapshot, built lazily."""
        return {
            snapshot: FxRates.from_market_context(self.market_context, snapshot)
            for snapshot in SNAPSHOTS
        }

    def fx_at(self, snapshot: str) -> FxRates:
        try:
            return self.fx[snapshot]
        except KeyError as exc:
            raise KeyError(
                f"{snapshot!r} is not a snapshot date; expected one of {SNAPSHOTS}"
            ) from exc

    def holdings_at(self, snapshot: str = LATEST_SNAPSHOT) -> pd.DataFrame:
        return self.holdings[self.holdings["snapshot_date"] == pd.Timestamp(snapshot)]


def load() -> Dataset:
    """Load the whole dataset. Cheap enough at this size to do eagerly."""
    notes = json.loads((DATA_DIR / "rm_notes.json").read_text())
    rm_notes = pd.DataFrame(notes)
    if "note_date" in rm_notes.columns:
        rm_notes["note_date"] = pd.to_datetime(rm_notes["note_date"], format="%Y-%m-%d")

    return Dataset(
        clients=_read_csv("clients.csv"),
        portfolios=_read_csv("portfolios.csv"),
        holdings=_read_csv("holdings.csv"),
        instruments=_read_csv("instruments.csv"),
        mandates=_read_csv("mandates.csv"),
        transactions=_read_csv("transactions.csv"),
        credit_facilities=_read_csv("credit_facilities.csv"),
        commitments=_read_csv("commitments.csv"),
        planned_cash_needs=_read_csv("planned_cash_needs.csv"),
        market_context=_read_csv("market_context.csv"),
        event_log=_read_csv("event_log.csv"),
        rm_notes=rm_notes,
    )
