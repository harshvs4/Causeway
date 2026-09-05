"""Technical signals from instruments.csv price history.

The dataset has exactly 5 price snapshots per instrument — not enough for
RSI (needs 30+) or MACD (needs 26+). We compute what the data honestly
supports: total return, recent momentum, period returns, and linear slope.

The stock-analyzer slope indicator is used for trend direction. All other
signals are computed directly so they remain self-explanatory to any RM.

Every output value traces back to a price column in instruments.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

# ── path patch so stock-analyzer indicators are importable without install ──
_STOCK_ANALYZER = Path(__file__).resolve().parents[2] / "stock-analyzer"
if str(_STOCK_ANALYZER) not in sys.path:
    sys.path.insert(0, str(_STOCK_ANALYZER))

try:
    from indicators import slope as _slope_fn  # type: ignore[import]
    _HAS_SLOPE = True
except ImportError:
    _HAS_SLOPE = False

# Snapshot dates in chronological order — matches instruments.csv column names.
SNAPSHOTS = [
    "2025-12-31",
    "2026-02-27",
    "2026-03-31",
    "2026-06-30",
    "2026-08-26",
]
PRICE_COLS = [f"price_{s}" for s in SNAPSHOTS]


class InstrumentSignals(TypedDict):
    instrument_id: str
    prices: dict[str, float]          # snapshot → price
    total_return_pct: float            # full period: first → last
    recent_return_pct: float           # last period only: snapshot[-2] → last
    period_returns: dict[str, float]   # each snapshot-to-snapshot return
    slope_per_period: float            # linear slope (price units / period)
    trend: str                         # "rising" | "falling" | "flat"
    best_period: str                   # snapshot boundary with highest return
    worst_period: str                  # snapshot boundary with lowest return
    note: str                          # plain-English summary for voice


def compute_all(instruments: pd.DataFrame) -> dict[str, InstrumentSignals]:
    """Return signals for every instrument that has at least 2 valid prices."""
    results: dict[str, InstrumentSignals] = {}

    for _, row in instruments.iterrows():
        iid = str(row["instrument_id"])
        prices_raw = {s: row.get(f"price_{s}") for s in SNAPSHOTS}
        prices = {s: float(v) for s, v in prices_raw.items() if pd.notna(v) and float(v) > 0}

        if len(prices) < 2:
            continue

        snapshots_present = [s for s in SNAPSHOTS if s in prices]
        price_series = [prices[s] for s in snapshots_present]

        first, last = price_series[0], price_series[-1]
        total_return_pct = round((last / first - 1) * 100, 2)

        # Recent return: last available period
        recent_return_pct = round((price_series[-1] / price_series[-2] - 1) * 100, 2)

        # Period-by-period returns
        period_returns: dict[str, float] = {}
        for i in range(1, len(snapshots_present)):
            label = f"{snapshots_present[i-1]}→{snapshots_present[i]}"
            period_returns[label] = round((price_series[i] / price_series[i - 1] - 1) * 100, 2)

        # Slope: linear fit over index positions
        if _HAS_SLOPE and len(price_series) >= 3:
            close_df = pd.DataFrame({"Close": price_series})
            slope_series = _slope_fn(close_df, n=len(price_series))
            slope_val = round(float(slope_series.iloc[-1]), 4)
        else:
            # Fallback: simple rise/run
            n = len(price_series)
            x = np.arange(n, dtype=float)
            slope_val = round(float(np.polyfit(x, price_series, 1)[0]), 4)

        trend = "rising" if slope_val > 0.05 else "falling" if slope_val < -0.05 else "flat"

        best = max(period_returns, key=lambda k: period_returns[k]) if period_returns else ""
        worst = min(period_returns, key=lambda k: period_returns[k]) if period_returns else ""

        # Human-readable note for voice/assist
        direction = "up" if total_return_pct >= 0 else "down"
        note = (
            f"Over the tracked period, this instrument is {direction} "
            f"{abs(total_return_pct):.1f}% in total. "
            f"The most recent period moved {recent_return_pct:+.1f}%. "
            f"Trend is {trend}."
        )

        results[iid] = InstrumentSignals(
            instrument_id=iid,
            prices=prices,
            total_return_pct=total_return_pct,
            recent_return_pct=recent_return_pct,
            period_returns=period_returns,
            slope_per_period=slope_val,
            trend=trend,
            best_period=best,
            worst_period=worst,
            note=note,
        )

    return results
