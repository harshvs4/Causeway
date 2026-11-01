"""What a pledged facility looks like on today's actual market.

collateral.py answers the same question against the five dated snapshots, and is
deliberately untouched: its numbers are pinned to four decimal places in tests
and a reproducible build depends on them staying that way. This is the second
code path, and it exists only when a live price source has been asked for.

The claim it makes is narrow, and says so. A derived mark is not a traded price;
it is the last known price moved by a named real factor through a published
sensitivity. So every fact here cites four things, and a reviewer can walk all
of them:

    the facility row            drawn, and the trigger it is measured against
    the live quote              the series, its move, and the moment it was taken
    the sensitivity rule        the beta or duration, and the reasoning for it
    the collateral holdings     the positions being re-marked

The facility's stated lending value reconciles exactly to the sum of
lending_value_base over its collateral portfolio - checked across all five
facilities in the dataset, ratio 1.000000 - and that is what makes recomputing
it from re-marked holdings legitimate rather than an approximation.
"""

from __future__ import annotations

from engine.live_feed import Quote
from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.marks import Mark, revalue_holdings
from engine.models import Fact, Source

MARKET_DATA = "market_data"
SENSITIVITIES = "market_sensitivities.yaml"

# Below this the re-mark is not worth a note of its own: a facility that moved a
# hundredth of a point is a facility that did not move.
MIN_SHIFT_PP = 0.01


def _quote_row(quote: Quote) -> dict:
    """A live quote rendered as a source row, so it resolves like any other."""
    return {
        "series_id": quote.series_id,
        "value": quote.value,
        "previous_close": quote.previous_close,
        "pct_change": quote.pct_change,
        "as_of": quote.as_of,
        "origin": quote.origin,
        "live": quote.live,
        "proxy_symbol": quote.proxy_symbol,
        "note": quote.note,
    }


def _rule_row(mark: Mark) -> dict:
    return {
        "rule_id": mark.rule_id,
        "mode": mark.mode,
        "factor": mark.factor,
        "coefficient": mark.coefficient,
        "why": mark.why,
    }


def extra_source_rows(quotes: dict[str, Quote],
                      marks: dict[str, Mark]) -> dict[str, dict]:
    """Non-CSV origins, keyed exactly as engine.sources.source_key would.

    Handed to resolve() so the console and the vault can show a live quote, and
    the rule applied to it, in the same drawer as a dataset row.
    """
    rows: dict[str, dict] = {}
    for quote in quotes.values():
        rows[f"{MARKET_DATA}::{quote.row_ref}"] = _quote_row(quote)
    for mark in marks.values():
        rows[f"{SENSITIVITIES}::{mark.rule_id}"] = _rule_row(mark)
    return rows


def run(
    dataset: Dataset,
    client_ids: tuple[str, ...] = (),
    quotes: dict[str, Quote] | None = None,
    marks: dict[str, Mark] | None = None,
) -> list[Fact]:
    if not quotes or not marks:
        return []                      # no live source asked for; nothing to say

    remarked = revalue_holdings(dataset, marks)
    if remarked.empty:
        return []

    snapshot_holdings = dataset.holdings_at(LATEST_SNAPSHOT)
    facilities = dataset.credit_facilities
    facilities = facilities[facilities["client_id"].isin(client_ids)]

    facts: list[Fact] = []
    for _, facility in facilities.iterrows():
        facility_id = str(facility["facility_id"])
        client_id = str(facility["client_id"])
        portfolio_id = str(facility["collateral_portfolio_id"])
        trigger = float(facility["margin_call_ltv_pct"])
        drawn = float(facility[f"drawn_{LATEST_SNAPSHOT}"])

        before_rows = snapshot_holdings[
            snapshot_holdings["portfolio_id"] == portfolio_id
        ]
        after_rows = remarked[remarked["portfolio_id"] == portfolio_id]
        lending_before = float(before_rows["lending_value_base"].sum())
        lending_after = float(after_rows["lending_value_base"].sum())
        if lending_before <= 0 or lending_after <= 0:
            continue

        ltv_before = drawn / lending_before * 100
        ltv_after = drawn / lending_after * 100
        shift_pp = ltv_after - ltv_before
        if abs(shift_pp) < MIN_SHIFT_PP:
            continue

        distance_pp = trigger - ltv_after

        # Which factor actually did this. Attribution by contribution, not by
        # picking the largest holding: a big position whose rule is `none`
        # contributed nothing and must not be named as the cause.
        contribution: dict[str, float] = {}
        for _, row in before_rows.iterrows():
            mark = marks.get(str(row["instrument_id"]))
            if mark is None or not mark.moved or not mark.factor:
                continue
            contribution[mark.factor] = contribution.get(mark.factor, 0.0) + (
                float(row["lending_value_base"]) * mark.price_pct / 100
            )
        if not contribution:
            continue
        lead_factor = max(contribution, key=lambda f: abs(contribution[f]))
        lead_quote = quotes.get(lead_factor)
        if lead_quote is None or lead_quote.pct_change is None:
            continue
        lead_move = lead_quote.pct_change

        # The rule carrying the biggest share of that factor's effect, so the
        # coefficient quoted in the text is the one that actually mattered.
        lead_mark: Mark | None = None
        lead_weight = 0.0
        for _, row in before_rows.iterrows():
            mark = marks.get(str(row["instrument_id"]))
            if mark is None or mark.factor != lead_factor or not mark.moved:
                continue
            weight = abs(float(row["lending_value_base"]) * mark.price_pct)
            if weight > lead_weight:
                lead_weight, lead_mark = weight, mark
        if lead_mark is None:
            continue

        if ltv_after >= trigger:
            standing = (
                " On these marks it crosses its trigger."
                if ltv_before < trigger
                else " It was over its trigger on the statement too."
            )
        else:
            standing = " That is still inside its trigger."

        sources = (
            Source(
                file="credit_facilities.csv",
                row_ref=facility_id,
                fields=("margin_call_ltv_pct", f"drawn_{LATEST_SNAPSHOT}",
                        f"lending_value_{LATEST_SNAPSHOT}"),
            ),
            Source(
                file=MARKET_DATA,
                row_ref=lead_quote.row_ref,
                fields=("value", "previous_close", "pct_change", "as_of", "origin"),
            ),
            Source(
                file=SENSITIVITIES,
                row_ref=lead_mark.rule_id,
                fields=("mode", "factor", "coefficient", "why"),
            ),
        ) + tuple(
            Source(
                file="holdings.csv",
                row_ref=(
                    f"{row['portfolio_id']}|{row['instrument_id']}|"
                    f"{row['snapshot_date'].strftime('%Y-%m-%d')}"
                ),
                fields=("lending_value_base", "market_value_base"),
            )
            for _, row in before_rows.iterrows()
        )

        facts.append(
            Fact(
                fact_id=f"F-{client_id.replace('-', '')}-LIVEMARK-{facility_id}",
                client_id=client_id,
                kind="collateral",
                headline=(
                    f"On today's marks {facility_id} is at {ltv_after:.2f}% LTV, "
                    f"{distance_pp:.2f} points from its {trigger:.2f}% trigger — "
                    f"{shift_pp:+.2f} points against the {ltv_before:.2f}% on the "
                    f"last statement.{standing}"
                ),
                detail=(
                    f"Collateral portfolio {portfolio_id} re-marks from "
                    f"{lending_before:,.0f} to {lending_after:,.0f} of lending "
                    f"value, with {drawn:,.0f} drawn against it. The largest "
                    f"contribution is {lead_factor}, which moved "
                    f"{lead_move:+.2f}% since its previous close, applied at "
                    f"{lead_mark.coefficient:.2f} under rule "
                    f"{lead_mark.rule_id}: \"{lead_mark.why}\" "
                    f"These are derived marks, not traded prices — each "
                    f"instrument is repriced from its last known price by a real "
                    f"factor move through a published sensitivity, and both are "
                    f"cited above."
                ),
                numbers={
                    "ltv_pct": ltv_after,
                    "ltv_on_statement_pct": ltv_before,
                    "shift_pp": shift_pp,
                    "trigger_pct": trigger,
                    "distance_pp": distance_pp,
                    "drawn": drawn,
                    "lending_value_marked": lending_after,
                    "lending_value_on_statement": lending_before,
                    "lead_factor_move_pct": lead_move,
                    "coefficient": lead_mark.coefficient,
                },
                # The rule's reasoning is a real field of a cited row, so
                # reproducing it is quotation rather than assertion - and
                # verify_quotes checks it against market_sensitivities.yaml,
                # which is what stops a beta being justified by invented prose.
                quotes=(lead_mark.why,),
                sources=sources,
                as_of=LATEST_SNAPSHOT,
                confidence="derived",
                severity=max(0, min(100, int(100 - distance_pp * 4))),
            )
        )
    return facts
