"""Where what the client says and what the book does disagree.

This is the analyzer that produces the sharpest finding in the dataset, and it
is also the easiest one to do badly, because "contradiction" invites prose. So
every rule here is a named check that binds a *stated* intent - from
clients.objectives, life_stage or source_of_wealth - to a *measured* quantity.
A rule that cannot produce a number does not fire. Nothing is inferred from
tone, and no candidate survives that fails its data check.

rm_notes are cited as supporting evidence only when a note shares a significant
token with the stated intent being tested, so the citation is derived rather
than chosen.
"""

from __future__ import annotations

import re

from engine.analyzers.lookthrough import (
    Exposure,
    client_book_usd,
    exposures_by_portfolio,
    exposures_for_client,
    normalise_name,
    parse_underlying,
)
from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

# Words that carry no identity in a source-of-wealth or objectives string.
_STOPWORDS = {
    "a", "an", "and", "the", "of", "in", "on", "to", "from", "for", "with",
    "inherited", "family", "group", "business", "businesses", "company",
    "entrepreneur", "founder", "co", "holding", "holdings", "unlisted",
    "second", "generation", "wealth", "sale", "shares", "share", "pte", "ltd",
    "inc", "corp", "tbk", "fund", "note", "ref", "index", "plc",
}
_TOKEN = re.compile(r"[a-z]{3,}")

# Anchored to the mandate single-position limit rather than a number chosen
# here: 15% is the bank's own stated view of "too much in one name", and every
# mandate in the dataset uses it. A threshold we invented would be the first
# thing a reviewer argued with.
BOOK_CONCENTRATION_PCT = 15.0
STEM_PREFIX = 5
PORTFOLIO_DOMINANCE_PCT = 50.0
ILLIQUID_CONCENTRATION_PCT = 50.0


def significant_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(str(text).lower()) if t not in _STOPWORDS}


def shared_terms(left: set[str], right: set[str]) -> set[str]:
    """Terms common to both sides, matching on a shared word stem.

    Exact matching misses the same relationship written slightly differently -
    a source of wealth recorded as "pharmaceutical group" against a holding
    named "Kanto Pharma Holdings", or "healthcare" against "Verdant Health".
    Requiring a shared prefix of STEM_PREFIX characters keeps the match
    mechanical and auditable; the matched pair is reported in the fact so a
    reviewer can see exactly what was joined and disagree with it.
    """
    exact = left & right
    matched: set[str] = set(exact)
    for x in left:
        for y in right:
            if x in exact or y in exact:
                continue          # already reported as a plain shared term
            if (
                min(len(x), len(y)) >= STEM_PREFIX
                and x[:STEM_PREFIX] == y[:STEM_PREFIX]
            ):
                matched.add(f"{x}/{y}")
    return matched


def _client_source(client_id: str, fields: tuple[str, ...]) -> Source:
    return Source(file="clients.csv", row_ref=client_id, fields=fields)


def _supporting_notes(dataset: Dataset, client_id: str,
                      tokens: set[str]) -> list[Source]:
    """Notes that mention the thing being tested. Derived, not hand-picked."""
    notes = dataset.rm_notes[dataset.rm_notes["client_id"] == client_id]
    out: list[Source] = []
    for _, note in notes.iterrows():
        if significant_tokens(note["note"]) & tokens:
            out.append(
                Source(file="rm_notes.json", row_ref=str(note["note_id"]),
                       fields=("note",))
            )
    return out


def _top(exposures: dict[str, Exposure]) -> Exposure | None:
    return max(exposures.values(), key=lambda e: e.total_base, default=None)


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0001",)) -> list[Fact]:
    facts: list[Fact] = []
    clients = dataset.clients.set_index("client_id")
    instruments = dataset.instruments.set_index("instrument_id")
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)

    for client_id in client_ids:
        client = clients.loc[client_id]
        book_usd = client_book_usd(dataset, client_id)
        if book_usd <= 0:
            continue
        book = exposures_for_client(dataset, client_id)
        biggest = _top(book)
        if biggest is None:
            continue
        biggest_pct = biggest.total_base / book_usd * 100
        biggest_key = normalise_name(biggest.display_name)

        # -- T1: the money is still in the thing that made the money ---------
        wealth_tokens = significant_tokens(client["source_of_wealth"])
        name_tokens = significant_tokens(biggest.display_name)
        sector_tokens: set[str] = set()
        for source in biggest.direct_rows + biggest.indirect_rows:
            instrument_id = source.row_ref.split("|")[1]
            if instrument_id in instruments.index:
                sector_tokens |= significant_tokens(
                    instruments.loc[instrument_id, "sector"]
                )
        overlap = shared_terms(wealth_tokens, name_tokens | sector_tokens)

        if overlap and biggest_pct >= BOOK_CONCENTRATION_PCT:
            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-TENSION-SOURCEOFWEALTH",
                    client_id=client_id,
                    kind="tension",
                    headline=(
                        f"The client's largest exposure and their source of wealth "
                        f"are the same bet: {biggest.display_name} is "
                        f"{biggest_pct:.2f}% of the book."
                    ),
                    detail=(
                        f"Source of wealth is recorded as "
                        f"\"{client['source_of_wealth']}\". The link is not a "
                        f"judgement call: the shared term(s) "
                        f"{', '.join(sorted(overlap))} appear in both that field and "
                        f"in the holding's name or sector. A shock to that industry "
                        f"hits the portfolio and the family's income at once."
                    ),
                    numbers={"book_pct": biggest_pct,
                             "exposure_usd": biggest.total_base},
                    quotes=(str(client["source_of_wealth"]),),
                    sources=tuple(
                        [_client_source(client_id, ("source_of_wealth",))]
                        + biggest.direct_rows
                        + biggest.indirect_rows
                        + _supporting_notes(dataset, client_id, wealth_tokens)
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="derived",
                    severity=min(100, int(biggest_pct + 40)),
                )
            )

        # -- T2: bought more of their own risk, inside a wrapper -------------
        per_portfolio = exposures_by_portfolio(
            dataset, client_id, value_column="market_value_usd"
        )
        dominated: dict[str, str] = {}          # name key -> portfolio it dominates
        for portfolio_id, bucket in per_portfolio.items():
            total = float(
                holdings[holdings["portfolio_id"] == portfolio_id]["market_value_usd"].sum()
            )
            if total <= 0:
                continue
            for key, exposure in bucket.items():
                if exposure.total_base / total * 100 >= PORTFOLIO_DOMINANCE_PCT:
                    dominated[key] = portfolio_id

        for _, row in holdings[holdings["client_id"] == client_id].iterrows():
            instrument = instruments.loc[row["instrument_id"]]
            basket = parse_underlying(instrument.get("underlying_reference"))
            if not basket:
                continue
            for name in basket:
                key = normalise_name(name)
                already = book.get(key)
                if already is None:
                    continue
                exposure_pct = already.total_base / book_usd * 100
                if exposure_pct < BOOK_CONCENTRATION_PCT:
                    continue

                wrapper_portfolio = str(row["portfolio_id"])
                home = dominated.get(key)
                boundary = ""
                if home and home != wrapper_portfolio:
                    boundary = (
                        f" The name dominates {home}, and this wrapper sits in a "
                        f"different account, {wrapper_portfolio} — so an exposure the "
                        f"client may think of as ring-fenced to one account is "
                        f"present in both."
                    )
                facts.append(
                    Fact(
                        fact_id=(
                            f"F-{client_id.replace('-', '')}-TENSION-WRAPPER-"
                            f"{row['instrument_id']}"
                        ),
                        client_id=client_id,
                        kind="tension",
                        headline=(
                            f"{row['instrument_id']} adds more {name} to a book already "
                            f"{exposure_pct:.2f}% exposed to it."
                        ),
                        detail=(
                            f"{instrument['instrument_name']} references {name} in its "
                            f"underlying basket, so the "
                            f"{float(row['market_value_usd']):,.0f} held in "
                            f"{wrapper_portfolio} is additional exposure to a name the "
                            f"client is already concentrated in, not a "
                            f"diversifier.{boundary}"
                        ),
                        numbers={
                            "existing_exposure_pct": exposure_pct,
                            "wrapper_usd": float(row["market_value_usd"]),
                        },
                        quotes=(str(instrument["instrument_name"]), str(name)),
                        sources=tuple(
                            [
                                Source(
                                    file="instruments.csv",
                                    row_ref=str(row["instrument_id"]),
                                    fields=("underlying_reference", "asset_class"),
                                ),
                                Source(
                                    file="holdings.csv",
                                    row_ref=(
                                        f"{row['portfolio_id']}|{row['instrument_id']}|"
                                        f"{row['snapshot_date'].strftime('%Y-%m-%d')}"
                                    ),
                                    fields=("market_value_usd", "weight_pct"),
                                ),
                            ]
                            + already.direct_rows
                            + _supporting_notes(
                                dataset, client_id, significant_tokens(name)
                            )
                        ),
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=min(100, int(exposure_pct + 45)),
                    )
                )

        # -- T3: stated diversification vs measured concentration ------------
        objectives = str(client["objectives"])
        if "diversif" in objectives.lower() and biggest_pct >= BOOK_CONCENTRATION_PCT:
            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-TENSION-DIVERSIFICATION",
                    client_id=client_id,
                    kind="tension",
                    headline=(
                        f"Diversification is a stated objective, but one name is "
                        f"{biggest_pct:.2f}% of the book."
                    ),
                    detail=(
                        f"Recorded objectives: \"{objectives}\". Measured against the "
                        f"holdings, the largest single exposure is "
                        f"{biggest.display_name} at {biggest.total_base:,.0f} of "
                        f"{book_usd:,.0f}. Whether that gap is deliberate is a "
                        f"conversation, not a data error — but it should be a "
                        f"conversation the RM has on purpose."
                    ),
                    numbers={"book_pct": biggest_pct,
                             "exposure_usd": biggest.total_base,
                             "book_usd": book_usd},
                    quotes=(objectives,),
                    sources=tuple(
                        [_client_source(client_id, ("objectives",))]
                        + biggest.direct_rows
                        + biggest.indirect_rows
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="derived",
                    severity=min(100, int(biggest_pct + 20)),
                )
            )

        # -- T4: pre-liquidity-event, with the wealth locked up --------------
        if "pre-liquidity" in str(client["life_stage"]).lower():
            for _, row in holdings[holdings["client_id"] == client_id].iterrows():
                if str(row["liquidity_tier"]) != "Illiquid":
                    continue
                position_pct = float(row["market_value_usd"]) / book_usd * 100
                if position_pct < ILLIQUID_CONCENTRATION_PCT:
                    continue
                facts.append(
                    Fact(
                        fact_id=(
                            f"F-{client_id.replace('-', '')}-TENSION-PRELIQUIDITY-"
                            f"{row['instrument_id']}"
                        ),
                        client_id=client_id,
                        kind="tension",
                        headline=(
                            f"{position_pct:.2f}% of the book is a single illiquid "
                            f"position while the client is pre-liquidity-event."
                        ),
                        detail=(
                            f"{row['instrument_name']} is carried at "
                            f"{float(row['market_value_usd']):,.0f} and is tiered "
                            f"Illiquid. Life stage is recorded as "
                            f"\"{client['life_stage']}\" with liquidity needs "
                            f"{client['liquidity_needs']}. Everything the client plans "
                            f"depends on an event that has not happened yet."
                        ),
                        numbers={
                            "position_pct": position_pct,
                            "position_usd": float(row["market_value_usd"]),
                        },
                        quotes=(
                            str(row["instrument_name"]),
                            str(client["life_stage"]),
                        ),
                        sources=(
                            _client_source(client_id, ("life_stage", "liquidity_needs")),
                            Source(
                                file="holdings.csv",
                                row_ref=(
                                    f"{row['portfolio_id']}|{row['instrument_id']}|"
                                    f"{row['snapshot_date'].strftime('%Y-%m-%d')}"
                                ),
                                fields=("market_value_usd", "liquidity_tier"),
                            ),
                        ),
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=min(100, int(position_pct + 15)),
                    )
                )
    return facts
