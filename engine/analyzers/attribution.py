"""Adapter: attribution rows -> Fact.

engine/attribution.py computes which dated event plausibly explains a holding's
move between two snapshots. It returns plain dataclasses, which is fine for a
computation but cannot reach the vault, a cue card or the voice layer: those
only carry Fact objects, and a Fact cannot be constructed without sources or
with a number it did not compute.

So this wraps it. Nothing here re-derives an attribution - it takes the rows as
given and binds each to the exact holdings rows and event_log entry behind it.
The alternative was a second, unchecked route to the screen, which would have
made the guardrail decorative.
"""

from __future__ import annotations

from engine import attribution as engine_attribution
from engine.loader import Dataset
from engine.models import Fact, Source

# An attribution below this confidence is a coincidence of dates, not an
# explanation. The underlying module scores 0-1 on transmission overlap.
MIN_CONFIDENCE = 0.4
# Small moves are noise. Only explain something worth explaining.
MIN_ABS_PCT = 3.0
# Facts per client, most material first.
MAX_PER_CLIENT = 4


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0002",)) -> list[Fact]:
    facts: list[Fact] = []
    rows = engine_attribution.compute(dataset)

    for client_id in client_ids:
        candidates = [
            row
            for row in rows
            if row.client_id == client_id
            and row.matched_event_date
            and row.confidence >= MIN_CONFIDENCE
            and abs(row.value_delta_pct) >= MIN_ABS_PCT
        ]
        candidates.sort(key=lambda r: -abs(r.value_delta_usd))

        for row in candidates[:MAX_PER_CLIENT]:
            direction = "fell" if row.value_delta_usd < 0 else "rose"
            magnitude = abs(row.value_delta_usd)

            # Which holdings rows actually produced the delta. Both ends of the
            # window are cited, because a change is a statement about two dates.
            holding_sources = tuple(
                Source(
                    file="holdings.csv",
                    row_ref=f"{portfolio}|{row.instrument_id}|{snapshot}",
                    fields=("market_value_usd", "price_local"),
                )
                for snapshot in (row.snapshot_from, row.snapshot_to)
                for portfolio in _portfolios_holding(
                    dataset, client_id, row.instrument_id, snapshot
                )
            )
            if not holding_sources:
                continue

            facts.append(
                Fact(
                    fact_id=(
                        f"F-{client_id.replace('-', '')}-ATTRIB-"
                        f"{row.instrument_id}-{row.snapshot_to}"
                    ),
                    client_id=client_id,
                    kind="attribution",
                    headline=(
                        f"{row.instrument_name} {direction} "
                        f"{abs(row.value_delta_pct):.2f}% between {row.snapshot_from} "
                        f"and {row.snapshot_to}, a move of "
                        f"{magnitude:,.0f} in USD terms."
                    ),
                    detail=(
                        f"The dated event that reaches this holding is: "
                        f"\"{row.matched_event_description}\" "
                        f"({row.matched_event_type}, severity "
                        f"{row.matched_event_severity}). It transmits through "
                        f"{row.transmission}. The link is a keyword overlap between "
                        f"that transmission channel and the instrument's own sector, "
                        f"region and asset class — scored {row.confidence:.2f} — not "
                        f"a judgement about causation. Price moved "
                        f"{row.price_from:,.2f} to {row.price_to:,.2f}."
                    ),
                    numbers={
                        "delta_pct": abs(row.value_delta_pct),
                        "delta_usd": magnitude,
                        "price_from": float(row.price_from),
                        "price_to": float(row.price_to),
                        "confidence": float(row.confidence),
                    },
                    quotes=(
                        str(row.matched_event_description),
                        str(row.transmission),
                    ),
                    sources=holding_sources
                    + (
                        Source(
                            file="event_log.csv",
                            row_ref=str(row.matched_event_date),
                            fields=("description", "primary_transmission", "severity"),
                        ),
                        Source(
                            file="instruments.csv",
                            row_ref=str(row.instrument_id),
                            fields=("sector", "region", "asset_class"),
                        ),
                    ),
                    as_of=str(row.snapshot_to),
                    confidence="derived",
                    severity=min(100, int(abs(row.value_delta_pct) * 2 + 30)),
                )
            )
    return facts


def _portfolios_holding(
    dataset: Dataset, client_id: str, instrument_id: str, snapshot: str
) -> list[str]:
    holdings = dataset.holdings_at(snapshot)
    match = holdings[
        (holdings["client_id"] == client_id)
        & (holdings["instrument_id"] == instrument_id)
    ]
    return [str(p) for p in match["portfolio_id"].unique()]
