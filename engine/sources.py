"""Resolve a Source back to the actual row it points at.

A Fact says *where* it came from; this turns that pointer into the row itself
so the vault and the console can both show a reviewer the underlying data
rather than asking them to take the fact's word for it.

Row-ref conventions, one per file:
    holdings.csv           portfolio_id|instrument_id|snapshot_date
    mandates.csv           mandate_code|asset_class
    instruments.csv        instrument_id
    transactions.csv       transaction_id
    portfolios.csv         portfolio_id
    clients.csv            client_id
    credit_facilities.csv  facility_id
    rm_notes.json          note_id
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.loader import Dataset

_KEYS: dict[str, tuple[str, ...]] = {
    "holdings.csv": ("portfolio_id", "instrument_id", "snapshot_date"),
    "instruments.csv": ("instrument_id",),
    "portfolios.csv": ("portfolio_id",),
    "clients.csv": ("client_id",),
    "credit_facilities.csv": ("facility_id",),
    "mandates.csv": ("mandate_code", "asset_class"),
    "transactions.csv": ("transaction_id",),
    "commitments.csv": ("commitment_id",),
    "planned_cash_needs.csv": ("need_id",),
    "event_log.csv": ("event_date",),
    "rm_notes.json": ("note_id",),
}

_FRAMES = {
    "holdings.csv": "holdings",
    "instruments.csv": "instruments",
    "portfolios.csv": "portfolios",
    "clients.csv": "clients",
    "credit_facilities.csv": "credit_facilities",
    "mandates.csv": "mandates",
    "transactions.csv": "transactions",
    "commitments.csv": "commitments",
    "planned_cash_needs.csv": "planned_cash_needs",
    "event_log.csv": "event_log",
    "rm_notes.json": "rm_notes",
}


class SourceNotFound(LookupError):
    """A Source points at a row that does not exist. Always a bug in an analyzer."""


def source_key(file: str, row_ref: str) -> str:
    return f"{file}::{row_ref}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def resolve(dataset: Dataset, file: str, row_ref: str) -> dict[str, Any]:
    """Return the single row a Source points at, as plain JSON-able values."""
    try:
        keys = _KEYS[file]
        frame: pd.DataFrame = getattr(dataset, _FRAMES[file])
    except KeyError as exc:
        raise SourceNotFound(f"no row-ref convention registered for {file!r}") from exc

    parts = row_ref.split("|")
    if len(parts) != len(keys):
        raise SourceNotFound(
            f"{file} row_ref {row_ref!r} has {len(parts)} part(s), expected "
            f"{len(keys)} for keys {keys}"
        )

    mask = pd.Series(True, index=frame.index)
    for key, part in zip(keys, parts, strict=True):
        column = frame[key]
        if pd.api.types.is_datetime64_any_dtype(column):
            mask &= column == pd.Timestamp(part)
        else:
            mask &= column.astype(str) == part

    matched = frame[mask]
    if len(matched) == 0:
        raise SourceNotFound(f"{file}: no row matches {row_ref!r}")
    if len(matched) > 1:
        raise SourceNotFound(f"{file}: {len(matched)} rows match {row_ref!r}, expected 1")

    return {column: _jsonable(value) for column, value in matched.iloc[0].items()}
