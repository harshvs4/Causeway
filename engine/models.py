"""The Fact contract.

Nothing renders anywhere in Anchorline that is not one of these objects. Two
invariants are enforced at construction time, so a fact that violates either
cannot exist in memory, let alone reach the vault or a voice cue:

  INVARIANT 1  every fact carries at least one Source
  INVARIANT 2  every number appearing in the rendered text also appears in
               `numbers` (i.e. it was computed, not written)

Invariant 2 is what makes "no claim without a traceable source" mechanical
rather than aspirational. Templates must therefore pass *every* value they
render through `numbers`, including scaled renderings: if the text says
"USD 1.7m" derived from 1_700_000, then `numbers` needs the 1.7 as well.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Confidence = Literal["verified", "derived", "scenario"]
FactKind = Literal[
    "attribution",
    "lookthrough",
    "mandate",
    "collateral",
    "liquidity",
    "tension",
    "triage",
    "scenario",
]

# Scrubbed before numeral extraction: these are identifiers and dates, not
# numeric claims. "CL-0002", "SYN-SP-0505", "N-004", "2026-06-30" must not be
# mistaken for figures that need sourcing.
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_IDENTIFIER = re.compile(r"\b[A-Z]{1,5}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_NUMERAL = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_ISO_DATE_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NumberNotSourced(ValueError):
    """A rendered number has no counterpart in `numbers`. Invariant 2 failed."""


def claimed_numbers(
    text: str, quotes: tuple[str, ...] = ()
) -> list[tuple[str, float, int]]:
    """Every numeral a reader would take as a factual claim.

    Returns (as_written, value, decimals_as_written). Removed first, because
    none of them is an authored claim:
      - identifiers ("CL-0002", "SYN-SP-0505", "N-004")
      - ISO dates ("2026-06-30")
      - verbatim quotations of source text, declared in `quotes`

    That last exemption exists because source fields legitimately contain
    numerals - an instrument is *named* "Fixed Coupon Note ref. Basket C,
    9.20% p.a., 12M", and an objective *says* "a property purchase in 2027".
    Reproducing them is quotation, not assertion, and the row they came from
    is cited anyway. Anything outside a declared quote is still a claim.
    """
    scrubbed = text
    for quote in quotes:
        scrubbed = scrubbed.replace(quote, " ")
    scrubbed = _IDENTIFIER.sub(" ", _ISO_DATE.sub(" ", scrubbed))
    found: list[tuple[str, float, int]] = []
    for match in _NUMERAL.finditer(scrubbed):
        written = match.group(0)
        cleaned = written.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:  # pragma: no cover - regex already constrains this
            continue
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        found.append((written, value, decimals))
    return found


def _is_supported(value: float, decimals: int, allowed: list[float]) -> bool:
    """True if `value` is some allowed number rendered at `decimals` precision.

    Rounding is legitimate rendering: a headline may show 24.64 for 24.6400, or
    75.6 for 75.6372. Rescaling is not - 1.7 does not stand in for 1_700_000,
    because that is where a plausible-looking wrong number would slip through.
    """
    return any(abs(round(candidate, decimals) - value) < 1e-9 for candidate in allowed)


class Source(BaseModel):
    """A pointer to the exact rows that produced a fact."""

    model_config = {"frozen": True}

    file: str = Field(min_length=1)          # "holdings.csv"
    row_ref: str = Field(min_length=1)       # "PF-0003|SYN-ST-0103|2026-08-26"
    fields: tuple[str, ...] = Field(min_length=1)  # ("market_value_base", "weight_pct")


class Fact(BaseModel):
    """One traceable claim."""

    model_config = {"frozen": True}

    fact_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    kind: FactKind
    headline: str = Field(min_length=1)
    detail: str = ""
    numbers: dict[str, float] = Field(default_factory=dict)
    # Verbatim strings copied out of a cited source row. Exempt from the
    # numeral check, and each must actually appear in the rendered text.
    quotes: tuple[str, ...] = ()
    # INVARIANT 1: a fact with no source cannot be constructed.
    sources: tuple[Source, ...] = Field(min_length=1)
    as_of: str
    confidence: Confidence
    severity: int = Field(ge=0, le=100)

    @field_validator("as_of")
    @classmethod
    def _as_of_is_iso(cls, value: str) -> str:
        if not _ISO_DATE_FULL.match(value):
            raise ValueError(f"as_of must be YYYY-MM-DD, got {value!r}")
        return value

    @model_validator(mode="after")
    def _every_rendered_number_is_computed(self) -> "Fact":
        """INVARIANT 2."""
        allowed = list(self.numbers.values())
        for field_name in ("headline", "detail"):
            text = getattr(self, field_name)
            for written, value, decimals in claimed_numbers(text, self.quotes):
                if not _is_supported(value, decimals, allowed):
                    raise NumberNotSourced(
                        f"{self.fact_id}: {field_name} claims {written!r} but no "
                        f"value in numbers renders to it. numbers={self.numbers}"
                    )
        return self

    @model_validator(mode="after")
    def _scenario_kind_implies_scenario_confidence(self) -> "Fact":
        """Forward-looking facts may never be filed as settled.

        The vault builder enforces the folder split; this stops the two from
        diverging at the source.
        """
        if self.kind == "scenario" and self.confidence != "scenario":
            raise ValueError(
                f"{self.fact_id}: kind='scenario' requires confidence='scenario', "
                f"got {self.confidence!r}"
            )
        return self

    @model_validator(mode="after")
    def _quotes_are_actually_quoted(self) -> "Fact":
        """A declared quote that appears nowhere is a hole in the check."""
        rendered = f"{self.headline} {self.detail}"
        for quote in self.quotes:
            if quote not in rendered:
                raise ValueError(
                    f"{self.fact_id}: declared quote {quote!r} does not appear in "
                    f"the rendered text, so it exempts nothing"
                )
        return self

    @property
    def is_verified(self) -> bool:
        """Eligible for vault/Verified/. Scenario output never is."""
        return self.confidence in ("verified", "derived")
