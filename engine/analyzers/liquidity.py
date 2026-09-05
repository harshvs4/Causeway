"""Can the client actually fund what they have promised?

Naively this is "cash needs vs liquid assets". The dataset makes it harder in
the way real books are harder: the liquid assets may be pledged. Selling
collateral to raise cash lowers lending value, which raises LTV, which can walk
a facility into a margin call. So "sellable" has two different meanings and the
gap between them is where the risk lives.

For a pledged portfolio we solve for the sale that would take LTV exactly to
its trigger:

    LTV_after = drawn / (lending_value - sold * advance_rate)
    sold_max  = (lending_value - drawn / trigger) / advance_rate

advance_rate is the lending-value-weighted rate of the daily-tradeable holdings
in that portfolio, because that is what would actually be sold.
"""

from __future__ import annotations

import pandas as pd

from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

DAILY = "Daily"


def _pledged_portfolios(dataset: Dataset, client_id: str) -> dict[str, pd.Series]:
    facilities = dataset.credit_facilities
    facilities = facilities[facilities["client_id"] == client_id]
    return {
        str(row["collateral_portfolio_id"]): row for _, row in facilities.iterrows()
    }


def sellable_before_breach(
    lending_value: float, drawn: float, trigger_pct: float, advance_rate: float
) -> float:
    """Market value that can be sold before LTV reaches the trigger."""
    if advance_rate <= 0:
        return 0.0
    lending_floor = drawn / (trigger_pct / 100)
    return max(0.0, (lending_value - lending_floor) / advance_rate)


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0001",)) -> list[Fact]:
    facts: list[Fact] = []
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)
    fx = dataset.fx_at(LATEST_SNAPSHOT)

    for client_id in client_ids:
        own = holdings[holdings["client_id"] == client_id]
        if own.empty:
            continue
        pledged = _pledged_portfolios(dataset, client_id)

        daily = own[own["liquidity_tier"] == DAILY]
        unencumbered = daily[~daily["portfolio_id"].isin(pledged)]
        unencumbered_usd = float(unencumbered["market_value_usd"].sum())
        encumbered_usd = float(
            daily[daily["portfolio_id"].isin(pledged)]["market_value_usd"].sum()
        )

        # --- how much collateral can actually be released -------------------
        release_capacity = 0.0
        for portfolio_id, facility in pledged.items():
            rows = own[(own["portfolio_id"] == portfolio_id)
                       & (own["liquidity_tier"] == DAILY)]
            market_value = float(rows["market_value_base"].sum())
            lending = float(rows["lending_value_base"].sum())
            if market_value <= 0:
                continue
            advance_rate = lending / market_value
            drawn = float(facility[f"drawn_{LATEST_SNAPSHOT}"])
            facility_lending = float(facility[f"lending_value_{LATEST_SNAPSHOT}"])
            trigger = float(facility["margin_call_ltv_pct"])
            sellable = sellable_before_breach(
                facility_lending, drawn, trigger, advance_rate
            )
            current_ltv = drawn / facility_lending * 100
            release_capacity += sellable

            facts.append(
                Fact(
                    fact_id=(
                        f"F-{client_id.replace('-', '')}-LIQUIDITY-PLEDGED-"
                        f"{facility['facility_id']}"
                    ),
                    client_id=client_id,
                    kind="liquidity",
                    headline=(
                        f"Only {sellable:,.0f} of {portfolio_id} can be sold before "
                        f"{facility['facility_id']} hits its {trigger:.2f}% trigger, "
                        f"against {market_value:,.0f} that looks daily-tradeable."
                    ),
                    detail=(
                        f"The portfolio is pledged as collateral. Selling reduces "
                        f"lending value at an average advance rate of "
                        f"{advance_rate * 100:.2f}%, so raising cash here raises LTV "
                        f"from its current {current_ltv:.2f}%. What appears liquid on "
                        f"a holdings report is {market_value:,.0f}; what is genuinely "
                        f"available without triggering a margin call is "
                        f"{sellable:,.0f}."
                    ),
                    numbers={
                        "sellable": sellable,
                        "apparent_daily": market_value,
                        "advance_rate_pct": advance_rate * 100,
                        "current_ltv_pct": current_ltv,
                        "trigger_pct": trigger,
                    },
                    sources=(
                        Source(
                            file="credit_facilities.csv",
                            row_ref=str(facility["facility_id"]),
                            fields=(
                                "margin_call_ltv_pct",
                                f"drawn_{LATEST_SNAPSHOT}",
                                f"lending_value_{LATEST_SNAPSHOT}",
                            ),
                        ),
                    )
                    + tuple(
                        Source(
                            file="holdings.csv",
                            row_ref=(
                                f"{r['portfolio_id']}|{r['instrument_id']}|"
                                f"{r['snapshot_date'].strftime('%Y-%m-%d')}"
                            ),
                            fields=("market_value_base", "lending_value_base",
                                    "advance_rate_pct", "liquidity_tier"),
                        )
                        for _, r in rows.iterrows()
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="derived",
                    severity=85,
                )
            )

        available_usd = unencumbered_usd + release_capacity

        # --- each promised outflow against what is genuinely available ------
        needs = dataset.planned_cash_needs
        needs = needs[needs["client_id"] == client_id]
        for _, need in needs.iterrows():
            amount_usd = fx.to_usd(float(need["amount"]), str(need["currency"]))
            shortfall = amount_usd - available_usd
            covered = shortfall <= 0

            if covered and unencumbered_usd >= amount_usd:
                verdict = (
                    f"Covered by unencumbered daily-tradeable assets of "
                    f"{unencumbered_usd:,.0f}, without touching pledged collateral."
                )
                severity = 25
            elif covered:
                verdict = (
                    f"Only coverable by selling pledged collateral: unencumbered "
                    f"daily assets are {unencumbered_usd:,.0f} and the release "
                    f"capacity of the pledged accounts is {release_capacity:,.0f}."
                )
                severity = 70
            else:
                verdict = (
                    f"Not fundable from liquid assets as things stand. Unencumbered "
                    f"daily assets are {unencumbered_usd:,.0f}; even selling pledged "
                    f"collateral to the edge of its trigger adds only "
                    f"{release_capacity:,.0f}, leaving {shortfall:,.0f} to find."
                )
                severity = 95

            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-LIQUIDITY-{need['need_id']}",
                    client_id=client_id,
                    kind="liquidity",
                    headline=(
                        f"{need['description']}: {need['currency']} "
                        f"{float(need['amount']):,.0f} due between "
                        f"{need['due_from'].strftime('%Y-%m-%d')} and "
                        f"{need['due_to'].strftime('%Y-%m-%d')}."
                    ),
                    detail=(
                        f"{amount_usd:,.0f} in USD terms. {verdict} Certainty is "
                        f"recorded as \"{need['certainty']}\". Daily-tradeable assets "
                        f"total {unencumbered_usd + encumbered_usd:,.0f}, of which "
                        f"{encumbered_usd:,.0f} sits in pledged accounts."
                    ),
                    numbers={
                        "amount_native": float(need["amount"]),
                        "amount_usd": amount_usd,
                        "unencumbered_usd": unencumbered_usd,
                        "encumbered_usd": encumbered_usd,
                        "release_capacity": release_capacity,
                        "shortfall": abs(shortfall),
                        "daily_total": unencumbered_usd + encumbered_usd,
                    },
                    quotes=(str(need["description"]), str(need["certainty"])),
                    sources=(
                        Source(
                            file="planned_cash_needs.csv",
                            row_ref=str(need["need_id"]),
                            fields=("amount", "currency", "due_from", "due_to",
                                    "certainty"),
                        ),
                    )
                    + tuple(
                        Source(
                            file="holdings.csv",
                            row_ref=(
                                f"{r['portfolio_id']}|{r['instrument_id']}|"
                                f"{r['snapshot_date'].strftime('%Y-%m-%d')}"
                            ),
                            fields=("market_value_usd", "liquidity_tier"),
                        )
                        for _, r in unencumbered.iterrows()
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="derived",
                    severity=severity,
                )
            )

        # --- uncalled private markets commitments ---------------------------
        commitments = dataset.commitments
        commitments = commitments[commitments["client_id"] == client_id]
        for _, commitment in commitments.iterrows():
            uncalled = float(commitment["uncalled"])
            uncalled_usd = fx.to_usd(uncalled, str(commitment["currency"]))
            facts.append(
                Fact(
                    fact_id=(
                        f"F-{client_id.replace('-', '')}-LIQUIDITY-"
                        f"{commitment['commitment_id']}"
                    ),
                    client_id=client_id,
                    kind="liquidity",
                    headline=(
                        f"{commitment['fund_name']} can call a further "
                        f"{commitment['currency']} {uncalled:,.0f} in the window "
                        f"{commitment['expected_call_window']}."
                    ),
                    detail=(
                        f"{uncalled_usd:,.0f} in USD terms against unencumbered "
                        f"daily-tradeable assets of {unencumbered_usd:,.0f}. Capital "
                        f"calls arrive on the fund's timetable, not the client's."
                    ),
                    numbers={
                        "uncalled_native": uncalled,
                        "uncalled_usd": uncalled_usd,
                        "unencumbered_usd": unencumbered_usd,
                    },
                    quotes=(str(commitment["fund_name"]),
                            str(commitment["expected_call_window"])),
                    sources=(
                        Source(
                            file="commitments.csv",
                            row_ref=str(commitment["commitment_id"]),
                            fields=("uncalled", "currency", "expected_call_window"),
                        ),
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="verified",
                    severity=60 if uncalled_usd > unencumbered_usd else 35,
                )
            )
    return facts
