"""Look-through concentration.

A structured product's asset class tells you what it *is*; underlying_reference
tells you what you are *exposed to*. This analyzer expands the second, so a
single name shows up at its true weight even when part of it is filed under
"Structured Products" and therefore invisible to every per-asset-class report.

Two things this must get right, both found by reading the actual strings:

1. Three grammars, not one. `Single underlying: X`, `Worst-of basket: X / Y / Z`
   and `Underlying: X, <terms>` all appear. A worst-of note carries downside to
   *every* name in its basket, so the full notional counts against each - the
   conservative treatment a credit officer would recognise.

2. Names must be normalised before matching. The dataset says
   `Pacific Orient Shipping` inside a basket and `Pacific Orient Shipping Ltd`
   as an instrument name. Exact matching splits one exposure into two and
   understates concentration, which is the exact failure this analyzer exists
   to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

# Corporate suffixes carry no identity and differ between the basket strings
# and the instrument names.
_SUFFIXES = {
    "inc", "ltd", "limited", "plc", "corp", "corporation", "co", "company",
    "pte", "llc", "lp", "sa", "nv", "ag", "ab", "as", "tbk", "bhd", "gmbh",
    "holdings", "group", "adr",
}
_PUNCT = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")

_GRAMMARS = (
    "worst-of basket:",
    "single underlying:",
    "underlying:",
)


def normalise_name(name: str) -> str:
    """Fold a company name to a comparable key."""
    text = _PUNCT.sub(" ", str(name).lower())
    tokens = [t for t in _WS.sub(" ", text).strip().split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


def parse_underlying(reference: str | float | None) -> list[str]:
    """Basket constituents named by an underlying_reference string.

    Returns [] when the reference describes terms rather than names (a strike,
    a vault location, a funding round) - silence is correct there, and better
    than inventing an exposure.
    """
    if reference is None or (isinstance(reference, float) and pd.isna(reference)):
        return []
    text = str(reference).strip()
    if not text:
        return []

    lowered = text.lower()
    for grammar in _GRAMMARS:
        if lowered.startswith(grammar):
            body = text[len(grammar):].strip()
            if grammar == "worst-of basket:":
                parts = [p.strip() for p in body.split("/")]
            else:
                # `Underlying: XAU spot, 100% capital protection...` - the name
                # is everything up to the first comma; the rest is terms.
                parts = [body.split(",")[0].strip()]
            return [p for p in parts if p]
    return []


@dataclass
class Exposure:
    """One name's exposure inside one portfolio."""

    display_name: str
    direct_base: float = 0.0
    indirect_base: float = 0.0
    direct_rows: list[Source] = field(default_factory=list)
    indirect_rows: list[Source] = field(default_factory=list)

    @property
    def total_base(self) -> float:
        return self.direct_base + self.indirect_base


def _holding_source(row: pd.Series, *, fields: tuple[str, ...]) -> Source:
    return Source(
        file="holdings.csv",
        row_ref=(
            f"{row['portfolio_id']}|{row['instrument_id']}|"
            f"{row['snapshot_date'].strftime('%Y-%m-%d')}"
        ),
        fields=fields,
    )


def exposures_by_portfolio(
    dataset: Dataset,
    client_id: str,
    snapshot: str = LATEST_SNAPSHOT,
    value_column: str = "market_value_base",
) -> dict[str, dict[str, Exposure]]:
    """{portfolio_id: {normalised_name: Exposure}} for one client at one date.

    Pass value_column="market_value_usd" to aggregate across portfolios whose
    base currencies differ - adding SGD to USD would otherwise be meaningless.
    """
    instruments = dataset.instruments.set_index("instrument_id")
    holdings = dataset.holdings_at(snapshot)
    holdings = holdings[holdings["client_id"] == client_id]

    result: dict[str, dict[str, Exposure]] = {}
    for _, row in holdings.iterrows():
        portfolio = row["portfolio_id"]
        bucket = result.setdefault(portfolio, {})
        instrument = instruments.loc[row["instrument_id"]]
        value = float(row[value_column])

        # The instrument itself, when it is a single name.
        if str(instrument.get("concentration_limit_applies", "N")).upper() == "Y":
            key = normalise_name(row["instrument_name"])
            exposure = bucket.setdefault(key, Exposure(display_name=row["instrument_name"]))
            exposure.direct_base += value
            exposure.direct_rows.append(
                _holding_source(row, fields=("market_value_base", "weight_pct"))
            )

        # Anything the instrument references.
        for name in parse_underlying(instrument.get("underlying_reference")):
            key = normalise_name(name)
            if not key:
                continue
            exposure = bucket.setdefault(key, Exposure(display_name=name))
            exposure.indirect_base += value
            exposure.indirect_rows.append(
                _holding_source(row, fields=("market_value_base", "weight_pct"))
            )
    return result


def exposures_for_client(
    dataset: Dataset, client_id: str, snapshot: str = LATEST_SNAPSHOT
) -> dict[str, Exposure]:
    """One name's exposure across every portfolio the client holds, in USD.

    This is the number that matters for a name that appears in more than one
    account: a per-portfolio view splits it and each half looks tolerable.
    """
    per_portfolio = exposures_by_portfolio(
        dataset, client_id, snapshot, value_column="market_value_usd"
    )
    merged: dict[str, Exposure] = {}
    for bucket in per_portfolio.values():
        for key, exposure in bucket.items():
            target = merged.setdefault(key, Exposure(display_name=exposure.display_name))
            target.direct_base += exposure.direct_base
            target.indirect_base += exposure.indirect_base
            target.direct_rows.extend(exposure.direct_rows)
            target.indirect_rows.extend(exposure.indirect_rows)
    return merged


def client_book_usd(dataset: Dataset, client_id: str,
                    snapshot: str = LATEST_SNAPSHOT) -> float:
    holdings = dataset.holdings_at(snapshot)
    return float(holdings[holdings["client_id"] == client_id]["market_value_usd"].sum())


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0002",)) -> list[Fact]:
    facts: list[Fact] = []
    portfolios = dataset.portfolios.set_index("portfolio_id")
    instruments = dataset.instruments.set_index("instrument_id")
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)

    for client_id in client_ids:
        # --- book level: one name across every account the client holds -----
        book_usd = client_book_usd(dataset, client_id)
        for exposure in sorted(
            exposures_for_client(dataset, client_id).values(),
            key=lambda e: -e.total_base,
        )[:1]:
            book_pct = exposure.total_base / book_usd * 100
            if book_pct < 25:
                continue
            direct_pct = exposure.direct_base / book_usd * 100
            indirect_pct = exposure.indirect_base / book_usd * 100
            accounts = sorted({s.row_ref.split("|")[0] for s in
                               exposure.direct_rows + exposure.indirect_rows})
            via = (
                f" — {direct_pct:.2f}% held directly and {indirect_pct:.2f}% "
                f"reached through a wrapper"
                if exposure.indirect_base > 0
                else ""
            )
            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-BOOKEXPOSURE",
                    client_id=client_id,
                    kind="lookthrough",
                    headline=(
                        f"{exposure.display_name} is {book_pct:.2f}% of the client's "
                        f"entire book{via}."
                    ),
                    detail=(
                        f"Measured across {len(accounts)} account(s) "
                        f"({', '.join(accounts)}) in USD, because a per-portfolio view "
                        f"splits the name and each half looks tolerable. "
                        f"{exposure.total_base:,.0f} of {book_usd:,.0f}."
                    ),
                    numbers={
                        "book_pct": book_pct,
                        "direct_pct": direct_pct,
                        "indirect_pct": indirect_pct,
                        "exposure_usd": exposure.total_base,
                        "book_usd": book_usd,
                        "accounts": float(len(accounts)),
                    },
                    sources=tuple(exposure.direct_rows + exposure.indirect_rows),
                    as_of=LATEST_SNAPSHOT,
                    confidence="derived",
                    severity=min(100, int(book_pct + 30)),
                )
            )

        by_portfolio = exposures_by_portfolio(dataset, client_id)
        for portfolio_id, exposures in sorted(by_portfolio.items()):
            portfolio_rows = holdings[holdings["portfolio_id"] == portfolio_id]
            portfolio_total = float(portfolio_rows["market_value_base"].sum())
            if portfolio_total <= 0:
                continue
            service_model = str(portfolios.loc[portfolio_id, "service_model"])

            for exposure in sorted(exposures.values(), key=lambda e: -e.total_base):
                combined_pct = exposure.total_base / portfolio_total * 100
                direct_pct = exposure.direct_base / portfolio_total * 100
                indirect_pct = exposure.indirect_base / portfolio_total * 100

                # The interesting case: look-through changes the picture.
                if exposure.indirect_base <= 0 or combined_pct < 10:
                    continue

                wrapper_ids = sorted(
                    {source.row_ref.split("|")[1] for source in exposure.indirect_rows}
                )
                wrapper_classes = sorted(
                    {
                        str(instruments.loc[iid, "asset_class"])
                        for iid in wrapper_ids
                        if iid in instruments.index
                    }
                )
                wrappers = ", ".join(wrapper_ids)
                classes = " / ".join(wrapper_classes)

                facts.append(
                    Fact(
                        fact_id=f"F-{client_id.replace('-', '')}-LOOKTHRU-{portfolio_id}",
                        client_id=client_id,
                        kind="lookthrough",
                        headline=(
                            f"{exposure.display_name} is {combined_pct:.2f}% of "
                            f"{portfolio_id} once {wrappers} is looked through — "
                            f"{direct_pct:.2f}% held directly, {indirect_pct:.2f}% "
                            f"through the wrapper."
                        ),
                        detail=(
                            f"The wrapper is filed under '{classes}', so a report that "
                            f"groups by asset class shows only the {direct_pct:.2f}% "
                            f"direct holding and misses the rest. Combined exposure is "
                            f"{exposure.total_base:,.0f} of {portfolio_total:,.0f} in "
                            f"portfolio base currency. {portfolio_id} is the "
                            f"{service_model} portfolio."
                        ),
                        numbers={
                            "combined_pct": combined_pct,
                            "direct_pct": direct_pct,
                            "indirect_pct": indirect_pct,
                            "combined_base": exposure.total_base,
                            "portfolio_total_base": portfolio_total,
                        },
                        sources=tuple(exposure.direct_rows + exposure.indirect_rows),
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=min(100, int(combined_pct * 3)),
                    )
                )

            # Single-position concentration, look-through or not.
            for exposure in sorted(exposures.values(), key=lambda e: -e.total_base)[:1]:
                combined_pct = exposure.total_base / portfolio_total * 100
                if combined_pct < 90 or exposure.indirect_base > 0:
                    continue
                liquidity = sorted(
                    {
                        str(instruments.loc[iid, "liquidity_tier"])
                        for iid in {s.row_ref.split("|")[1] for s in exposure.direct_rows}
                        if iid in instruments.index
                    }
                )
                facts.append(
                    Fact(
                        fact_id=f"F-{client_id.replace('-', '')}-CONCENTRATION-{portfolio_id}",
                        client_id=client_id,
                        kind="lookthrough",
                        headline=(
                            f"{portfolio_id} is {combined_pct:.2f}% a single position: "
                            f"{exposure.display_name}."
                        ),
                        detail=(
                            f"{exposure.total_base:,.0f} in portfolio base currency, "
                            f"liquidity tier {' / '.join(liquidity)}. As a "
                            f"{service_model} account it sits outside any mandate test, "
                            f"but it is part of the client's total wealth picture."
                        ),
                        numbers={
                            "combined_pct": combined_pct,
                            "combined_base": exposure.total_base,
                        },
                        sources=tuple(exposure.direct_rows),
                        as_of=LATEST_SNAPSHOT,
                        confidence="verified",
                        severity=min(100, int(combined_pct)),
                    )
                )
    return facts
