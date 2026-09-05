"""Attribution engine: link market events to holding value changes.

For each (client, instrument) pair, computes snapshot-to-snapshot value
deltas and matches them to event_log.csv entries using:
  1. Date containment  — event fell inside the snapshot window
  2. Transmission overlap — event's primary_transmission shares keywords
     with the instrument's sector, region, or asset class

No LLM. Every attribution traces to a real event_log row and real
holdings rows. Confidence is a deterministic score, not a probability.

Typical use:
    from engine import loader, attribution
    ds = loader.load()
    rows = attribution.compute(ds)
    # rows is a list of AttributionRow dicts, one per (client, instrument,
    # snapshot-pair) that has both a value change and a matched event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

# ── keyword index ─────────────────────────────────────────────────────────────
# Maps lower-cased instrument metadata tokens → transmission keywords to match.
# Kept small and explicit so every match is auditable.

_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "energy":                  ["energy", "oil", "lng", "shipping", "gulf"],
    "information technology":  ["technology", "tech", "equity"],
    "gold":                    ["gold", "precious metals"],
    "precious metals":         ["gold", "precious metals"],
    "consumer discretionary":  ["consumer", "luxury"],
    "financials":              ["credit", "lending", "fixed income"],
    "fixed income":            ["fixed income", "duration", "rate", "credit"],
    "alternatives":            ["alternatives", "private credit"],
    "real estate":             ["real estate", "property"],
    "utilities":               ["utilities", "infrastructure"],
    "health care":             ["health", "pharma"],
    "industrials":             ["defence", "airlines", "transport", "shipping"],
}

_REGION_KEYWORDS: dict[str, list[str]] = {
    "middle east":   ["gulf", "energy", "em credit"],
    "europe":        ["european", "eur", "ecb"],
    "north america": ["usd", "us technology", "growth equity"],
    "asia":          ["em credit", "asia"],
    "asia ex-japan": ["em credit", "asia"],
    "global":        [],  # matches everything weakly
    "japan":         [],
}

_CLASS_KEYWORDS: dict[str, list[str]] = {
    "equity":              ["equity", "technology", "growth equity"],
    "fixed income":        ["fixed income", "duration", "credit", "rate"],
    "alternatives":        ["alternatives", "private credit", "semi-liquid"],
    "cash":                [],
    "structured product":  ["structured products", "oil-linked"],
    "commodity":           ["gold", "precious metals", "energy"],
}


def _transmission_keywords(transmission: str) -> set[str]:
    """Lower-cased token set from a primary_transmission string."""
    return {t.strip().lower() for t in transmission.split(",")}


def _instrument_keywords(row: pd.Series) -> set[str]:
    """Keywords derived from an instrument's metadata."""
    keys: set[str] = set()
    for field in ("sector", "region", "asset_class", "sub_asset_class"):
        val = str(row.get(field, "") or "").lower().strip()
        if not val:
            continue
        keys.add(val)
        for lookup in (_SECTOR_KEYWORDS, _REGION_KEYWORDS, _CLASS_KEYWORDS):
            keys.update(lookup.get(val, []))
    return keys


def _overlap_score(inst_keys: set[str], event_trans: str) -> float:
    """Fraction of transmission keywords that match instrument metadata."""
    trans_keys = _transmission_keywords(event_trans)
    if not trans_keys:
        return 0.0
    matches = sum(
        1 for tk in trans_keys
        if any(tk in ik or ik in tk for ik in inst_keys)
    )
    return round(matches / len(trans_keys), 3)


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class AttributionRow:
    client_id: str
    instrument_id: str
    instrument_name: str
    snapshot_from: str          # ISO date
    snapshot_to: str            # ISO date
    value_delta_usd: float      # positive = gain, negative = loss
    value_delta_pct: float      # % change
    price_from: float
    price_to: float
    matched_event_date: Optional[str]
    matched_event_description: Optional[str]
    matched_event_type: Optional[str]
    matched_event_severity: Optional[str]
    transmission: Optional[str]
    confidence: float           # 0–1; 0 = no event matched

    def as_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "instrument_id": self.instrument_id,
            "instrument_name": self.instrument_name,
            "snapshot_from": self.snapshot_from,
            "snapshot_to": self.snapshot_to,
            "value_delta_usd": round(self.value_delta_usd, 2),
            "value_delta_pct": round(self.value_delta_pct, 2),
            "price_from": self.price_from,
            "price_to": self.price_to,
            "matched_event_date": self.matched_event_date,
            "matched_event_description": self.matched_event_description,
            "matched_event_type": self.matched_event_type,
            "matched_event_severity": self.matched_event_severity,
            "transmission": self.transmission,
            "confidence": self.confidence,
        }


# ── main computation ──────────────────────────────────────────────────────────

def compute(ds) -> list[AttributionRow]:
    """Return attribution rows for every (client, instrument, snapshot-pair)."""
    holdings = ds.holdings.copy()
    holdings["snapshot_date"] = pd.to_datetime(holdings["snapshot_date"])

    events = ds.event_log.copy()
    events["event_date"] = pd.to_datetime(events["event_date"])

    instruments = ds.instruments.set_index("instrument_id")

    # Build instrument keyword map once
    inst_keys_map: dict[str, set[str]] = {}
    for iid, row in instruments.iterrows():
        inst_keys_map[str(iid)] = _instrument_keywords(row)

    snapshots = sorted(holdings["snapshot_date"].unique())
    results: list[AttributionRow] = []

    for i in range(1, len(snapshots)):
        snap_from = snapshots[i - 1]
        snap_to = snapshots[i]

        # Aggregate across portfolios: sum value, take last price (same instrument)
        def _agg(df: pd.DataFrame) -> pd.DataFrame:
            return (
                df.groupby(["client_id", "instrument_id"])
                .agg(
                    market_value_usd=("market_value_usd", "sum"),
                    price_local=("price_local", "last"),
                    instrument_name=("instrument_name", "first"),
                )
            )

        h_from = _agg(holdings[holdings["snapshot_date"] == snap_from])
        h_to = _agg(holdings[holdings["snapshot_date"] == snap_to])

        # Events whose date falls inside this window (inclusive)
        window_events = events[
            (events["event_date"] >= snap_from) & (events["event_date"] <= snap_to)
        ]

        # Only pairs present in both snapshots
        common = h_from.index.intersection(h_to.index)

        for client_id, instrument_id in common:
            row_from = h_from.loc[(client_id, instrument_id)]
            row_to = h_to.loc[(client_id, instrument_id)]

            v_from = float(row_from["market_value_usd"])
            v_to = float(row_to["market_value_usd"])
            delta_usd = v_to - v_from
            delta_pct = (delta_usd / v_from * 100) if v_from else 0.0

            # Skip trivial changes (< 0.5%)
            if abs(delta_pct) < 0.5:
                continue

            price_from = float(row_from["price_local"])
            price_to = float(row_to["price_local"])
            inst_name = str(row_to.get("instrument_name") or instrument_id)

            # Match events
            inst_keys = inst_keys_map.get(str(instrument_id), set())
            best_event = None
            best_score = 0.0

            for _, ev in window_events.iterrows():
                trans = str(ev.get("primary_transmission", "") or "")
                score = _overlap_score(inst_keys, trans)

                # Boost for severity
                sev = str(ev.get("severity", "")).lower()
                if sev == "severe":
                    score = min(1.0, score + 0.15)
                elif sev == "high":
                    score = min(1.0, score + 0.05)

                if score > best_score:
                    best_score = score
                    best_event = ev

            results.append(AttributionRow(
                client_id=str(client_id),
                instrument_id=str(instrument_id),
                instrument_name=inst_name,
                snapshot_from=str(snap_from.date()),
                snapshot_to=str(snap_to.date()),
                value_delta_usd=delta_usd,
                value_delta_pct=delta_pct,
                price_from=price_from,
                price_to=price_to,
                matched_event_date=str(best_event["event_date"].date()) if best_event is not None else None,
                matched_event_description=str(best_event["description"]) if best_event is not None else None,
                matched_event_type=str(best_event["event_type"]) if best_event is not None else None,
                matched_event_severity=str(best_event["severity"]) if best_event is not None else None,
                transmission=str(best_event["primary_transmission"]) if best_event is not None else None,
                confidence=best_score,
            ))

    return results


def for_client(ds, client_id: str) -> list[dict]:
    """Convenience: attribution rows for one client, as dicts, most recent first."""
    rows = compute(ds)
    return [
        r.as_dict() for r in rows
        if r.client_id == client_id
    ]
