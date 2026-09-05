"""Is a number somebody said actually one the engine computed?

Used to check what a voice agent says against the facts it was given. The hard
part is that people legitimately restate a figure rather than reciting it:
13,207,200 becomes "13.2 million", 240,726 becomes "about 240,000", a shortfall
of 1,759,274 becomes "1.76". None of those is a fabrication, and a checker that
flags them is worse than useless - it buries a real hallucination in noise at
exactly the moment the distinction matters.

So a claimed number is supported if any grounded value can reach it by:
  - being written at the claimed precision      24.6400 -> "24.64"
  - being scaled by a thousand/million/billion  13,207,200 -> "13.2"
  - being rounded to a coarser magnitude        240,726 -> "240,000"

and any combination of the last two. Everything else is unsupported.

This is deliberately more permissive than the Fact invariant in engine.models,
which refuses rescaling outright. That strictness is right for text the engine
*authors*, because there it controls the template. Here we are checking text a
language model produced from tool output, where restatement is normal speech.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Identifiers and ISO dates are not numeric claims.
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_IDENTIFIER = re.compile(r"\b[A-Z]{1,5}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_NUMERAL = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

SCALES = (1.0, 1e2, 1e3, 1e4, 1e6, 1e9)
MAGNITUDES = range(0, 10)          # round to units, tens, hundreds, thousands...


def claimed(text: str) -> list[tuple[str, float, int]]:
    """(as_written, value, decimals) for every numeral read as a claim."""
    scrubbed = _IDENTIFIER.sub(" ", _ISO_DATE.sub(" ", text))
    found: list[tuple[str, float, int]] = []
    for match in _NUMERAL.finditer(scrubbed):
        written = match.group(0).rstrip(",.")     # "6,500,000," -> "6,500,000"
        cleaned = written.replace(",", "")
        if not cleaned or cleaned in ("-",):
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        found.append((written, value, decimals))
    return found


@dataclass(frozen=True)
class Source:
    """One number the agent could legitimately have been handed, and where from."""

    value: float
    origin: str          # "credit_facilities.csv::CF-0001.utilisation_pct_current"

    def __str__(self) -> str:
        return f"{self.value:g} from {self.origin}"


@dataclass(frozen=True)
class Claim:
    """A number somebody said, and what (if anything) accounts for it."""

    written: str
    value: float
    decimals: int
    supported_by: Source | None = None

    @property
    def grounded(self) -> bool:
        return self.supported_by is not None

    def __str__(self) -> str:
        if self.supported_by is None:
            return f"{self.written} — UNGROUNDED"
        return f"{self.written} matches {self.supported_by.origin}"


def _as_sources(allowed: Iterable[float | Source]) -> list[Source]:
    return [
        item if isinstance(item, Source) else Source(float(item), "unattributed")
        for item in allowed
    ]


def supporting_source(
    value: float, decimals: int, allowed: Iterable[float | Source]
) -> Source | None:
    """The first source that can be restated as this claim, or None."""
    for source in _as_sources(allowed):
        candidate = source.value
        if candidate == 0 and value == 0:
            return source
        for scale in SCALES:
            scaled = candidate / scale
            if abs(round(scaled, decimals) - value) < 1e-9:
                return source
            for magnitude in MAGNITUDES:
                if abs(round(scaled, -magnitude) - value) < 1e-9:
                    return source
    return None


def explain(
    text: str,
    allowed: Iterable[float | Source],
    ignore_small_integers: bool = True,
) -> list[Claim]:
    """Every number in `text`, each with the row and field that accounts for it.

    This is the form to show a reviewer. "72.22 matches
    credit_facilities.csv::CF-0001.utilisation_pct_current" is evidence; a bare
    pass is an assertion, and the whole point of this system is not asking
    anyone to take an assertion on trust.
    """
    sources = _as_sources(allowed)
    out: list[Claim] = []
    for written, value, decimals in claimed(text):
        if ignore_small_integers and decimals == 0 and abs(value) < 10_000:
            continue
        out.append(
            Claim(written, value, decimals, supporting_source(value, decimals, sources))
        )
    return out


def is_supported(value: float, decimals: int, allowed: Iterable[float]) -> bool:
    """Can any allowed value be restated as this claim?"""
    for candidate in allowed:
        if candidate == 0 and value == 0:
            return True
        for scale in SCALES:
            scaled = candidate / scale
            # written at the claimed precision
            if abs(round(scaled, decimals) - value) < 1e-9:
                return True
            # rounded to a coarser magnitude, e.g. 240,726 -> 240,000
            for magnitude in MAGNITUDES:
                if abs(round(scaled, -magnitude) - value) < 1e-9:
                    return True
    return False


def ungrounded(text: str, allowed: Iterable[float],
               ignore_small_integers: bool = True) -> list[str]:
    """Numbers in `text` that no allowed value can account for.

    Small bare integers are ignored by default: years, counts, "two decimal
    places", and ordinary conversational digits are not portfolio claims.
    """
    return [
        claim.written
        for claim in explain(text, allowed, ignore_small_integers)
        if not claim.grounded
    ]


def grounded_values(facts: Iterable[dict], client_id: str | None = None) -> set[float]:
    """Every number the engine computed, optionally for one client.

    This is the strict set: values a Fact asserts. Use it to check text the
    engine authored. It is NOT the right set for checking what a voice agent
    said, because the agent is handed more than this - see retrievable_values.
    """
    values: set[float] = set()
    for fact in facts:
        if client_id and fact.get("client_id") != client_id:
            continue
        values.update(float(v) for v in (fact.get("numbers") or {}).values())
    return values


def grounded_sources(facts: Iterable[dict], client_id: str | None = None) -> list[Source]:
    """Computed values, each attributed to the fact and key that produced it."""
    out: list[Source] = []
    for fact in facts:
        if client_id and fact.get("client_id") != client_id:
            continue
        for key, value in (fact.get("numbers") or {}).items():
            out.append(Source(float(value), f"{fact['fact_id']}.{key}"))
    return out


def retrievable_sources(envelope: dict, client_id: str | None = None) -> list[Source]:
    """Everything the agent could have been handed, each with its provenance."""
    facts = [
        f for f in envelope.get("facts", [])
        if not client_id or f.get("client_id") == client_id
    ]
    out = grounded_sources(facts)
    cited = {
        f"{source['file']}::{source['row_ref']}"
        for fact in facts
        for source in fact.get("sources", [])
    }
    for key in sorted(cited):
        for field, raw in (envelope.get("source_rows", {}).get(key) or {}).items():
            if isinstance(raw, bool) or raw is None:
                continue
            try:
                out.append(Source(float(raw), f"{key}.{field}"))
            except (TypeError, ValueError):
                continue
    return out


def retrievable_values(envelope: dict, client_id: str | None = None) -> set[float]:
    """Everything an agent could legitimately have been handed for one client.

    get_fact returns a Fact's computed numbers *and* the full source rows it
    cites, so a field the engine never computed is still something the agent
    can read and state truthfully. CF-0001 carries utilisation_pct_current
    (drawn / credit limit) alongside the LTV the engine derives (drawn /
    lending value); both are real, they are different measures, and only one
    is in `numbers`.

    Checking a transcript against `grounded_values` alone therefore reports
    honest retrieval as fabrication. This widens the set to the rows actually
    cited - and no further, so a number from some row the agent never saw is
    still caught.
    """
    facts = [
        f for f in envelope.get("facts", [])
        if not client_id or f.get("client_id") == client_id
    ]
    values = grounded_values(facts)
    cited = {
        f"{source['file']}::{source['row_ref']}"
        for fact in facts
        for source in fact.get("sources", [])
    }
    for key in cited:
        for raw in (envelope.get("source_rows", {}).get(key) or {}).values():
            if isinstance(raw, bool) or raw is None:
                continue
            try:
                values.add(float(raw))
            except (TypeError, ValueError):
                continue
    return values
