"""Mandate governance: bands, single-position limits, and binding exclusions.

Three rules the dataset is explicit about and that are easy to get wrong:

  - Custody accounts are not managed against a mandate. They are excluded from
    every test here, and still counted in the wealth picture elsewhere.
  - max_single_position_pct applies where concentration_limit_applies is Y.
    It is meant for single-name and single-asset exposures, not diversified
    funds, sovereign bonds or deposits.
  - A single-name limit has to be measured *after* look-through, or a wrapper
    on a name the portfolio already holds slips under it.

Whether a breach is drift or client-directed cannot be settled from trading
history here: transactions.csv holds one Buy row in total and is an income and
fee ledger, not a blotter. Structured product subscriptions and facility
drawdowns are the only client-directed actions it does record, so those are
what the classification uses.
"""

from __future__ import annotations

import pandas as pd

from engine.analyzers.lookthrough import exposures_by_portfolio
from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

CUSTODY = "Custody"
CLIENT_DIRECTED_TYPES = {"Structured Product Subscription", "Facility Drawdown"}


def _mandate_source(mandate_code: str, asset_class: str) -> Source:
    return Source(
        file="mandates.csv",
        row_ref=f"{mandate_code}|{asset_class}",
        fields=("min_pct", "target_pct", "max_pct", "max_single_position_pct"),
    )


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0001",)) -> list[Fact]:
    facts: list[Fact] = []
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)
    instruments = dataset.instruments.set_index("instrument_id")
    mandates = dataset.mandates
    portfolios = dataset.portfolios

    for client_id in client_ids:
        owned = portfolios[portfolios["client_id"] == client_id]
        subscriptions = dataset.transactions[
            (dataset.transactions["client_id"] == client_id)
            & (dataset.transactions["transaction_type"].isin(CLIENT_DIRECTED_TYPES))
        ]

        for _, portfolio in owned.iterrows():
            portfolio_id = str(portfolio["portfolio_id"])
            if str(portfolio["service_model"]) == CUSTODY:
                continue                      # not managed against a mandate
            mandate_code = str(portfolio["mandate_code"])
            bands = mandates[mandates["mandate_code"] == mandate_code]
            rows = holdings[holdings["portfolio_id"] == portfolio_id]
            total = float(rows["market_value_base"].sum())
            if total <= 0 or bands.empty:
                continue

            # --- asset-class bands ------------------------------------------
            actual = (
                rows.groupby("asset_class")["market_value_base"].sum() / total * 100
            )
            for _, band in bands.iterrows():
                asset_class = str(band["asset_class"])
                weight = float(actual.get(asset_class, 0.0))
                low, high = float(band["min_pct"]), float(band["max_pct"])
                if low <= weight <= high:
                    continue
                over = weight > high
                distance = weight - high if over else low - weight
                facts.append(
                    Fact(
                        fact_id=(
                            f"F-{client_id.replace('-', '')}-MANDATE-{portfolio_id}-"
                            f"{asset_class.replace(' ', '')}"
                        ),
                        client_id=client_id,
                        kind="mandate",
                        headline=(
                            f"{portfolio_id} holds {weight:.2f}% in {asset_class}, "
                            f"{'above' if over else 'below'} the "
                            f"{mandate_code} band of {low:.2f}% to {high:.2f}%."
                        ),
                        detail=(
                            f"Out of band by {distance:.2f} points against a target of "
                            f"{float(band['target_pct']):.2f}%. Measured on "
                            f"{total:,.0f} of portfolio base currency at "
                            f"{LATEST_SNAPSHOT}."
                        ),
                        numbers={
                            "weight_pct": weight,
                            "min_pct": low,
                            "max_pct": high,
                            "target_pct": float(band["target_pct"]),
                            "distance_pp": distance,
                            "portfolio_total": total,
                        },
                        sources=(
                            _mandate_source(mandate_code, asset_class),
                            Source(
                                file="portfolios.csv",
                                row_ref=portfolio_id,
                                fields=("mandate_code", "service_model"),
                            ),
                        )
                        + tuple(
                            Source(
                                file="holdings.csv",
                                row_ref=(
                                    f"{r['portfolio_id']}|{r['instrument_id']}|"
                                    f"{r['snapshot_date'].strftime('%Y-%m-%d')}"
                                ),
                                fields=("market_value_base", "asset_class"),
                            )
                            for _, r in rows[rows["asset_class"] == asset_class].iterrows()
                        ),
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=min(100, 40 + int(distance * 2)),
                    )
                )

            # --- single-position limit, measured after look-through ---------
            limit = float(bands["max_single_position_pct"].iloc[0])
            exposures = exposures_by_portfolio(dataset, client_id).get(portfolio_id, {})
            for exposure in sorted(exposures.values(), key=lambda e: -e.total_base):
                weight = exposure.total_base / total * 100
                if weight <= limit:
                    continue
                instrument_ids = sorted(
                    {s.row_ref.split("|")[1]
                     for s in exposure.direct_rows + exposure.indirect_rows}
                )
                if not any(
                    str(instruments.loc[i, "concentration_limit_applies"]).upper() == "Y"
                    for i in instrument_ids
                    if i in instruments.index
                ):
                    continue                  # limit is not meant for diversified funds

                directed = subscriptions[
                    subscriptions["instrument_id"].isin(instrument_ids)
                ]
                if not directed.empty:
                    provenance = (
                        f"At least part of this position is client-directed: "
                        f"{len(directed)} subscription(s) recorded in transactions."
                    )
                    directed_sources = tuple(
                        Source(
                            file="transactions.csv",
                            row_ref=str(t["transaction_id"]),
                            fields=("transaction_type", "instrument_id", "amount"),
                        )
                        for _, t in directed.iterrows()
                    )
                else:
                    provenance = (
                        "No client-directed subscription is recorded against it, so on "
                        "the evidence available this is drift rather than instruction."
                    )
                    directed_sources = ()

                through_wrapper = (
                    f" {exposure.indirect_base / total * 100:.2f} points of this is "
                    f"reached through a wrapper and would not appear on a report that "
                    f"reads asset class alone."
                    if exposure.indirect_base > 0
                    else ""
                )
                facts.append(
                    Fact(
                        fact_id=(
                            f"F-{client_id.replace('-', '')}-MANDATE-SINGLEPOS-"
                            f"{portfolio_id}-{instrument_ids[0]}"
                        ),
                        client_id=client_id,
                        kind="mandate",
                        headline=(
                            f"{exposure.display_name} is {weight:.2f}% of "
                            f"{portfolio_id}, above the {mandate_code} single-position "
                            f"limit of {limit:.2f}%."
                        ),
                        detail=(
                            f"Measured after look-through.{through_wrapper} "
                            f"{provenance}"
                        ),
                        numbers={
                            "weight_pct": weight,
                            "limit_pct": limit,
                            "excess_pp": weight - limit,
                            "indirect_pct": exposure.indirect_base / total * 100,
                            "directed_subscriptions": float(len(directed)),
                        },
                        sources=(
                            _mandate_source(mandate_code, str(bands["asset_class"].iloc[0])),
                        )
                        + tuple(exposure.direct_rows + exposure.indirect_rows)
                        + directed_sources,
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=min(100, 55 + int((weight - limit) * 2)),
                    )
                )

            # --- in-band confirmation ---------------------------------------
            # A clean portfolio is worth stating. "Nothing is out of band" is
            # information an RM needs before a meeting, and saying it here means
            # silence always signals "not checked" rather than "checked, fine".
            breached = [
                str(band["asset_class"])
                for _, band in bands.iterrows()
                if not (
                    float(band["min_pct"])
                    <= float(actual.get(str(band["asset_class"]), 0.0))
                    <= float(band["max_pct"])
                )
            ]
            if not breached:
                facts.append(
                    Fact(
                        fact_id=(
                            f"F-{client_id.replace('-', '')}-MANDATE-INBAND-"
                            f"{portfolio_id}"
                        ),
                        client_id=client_id,
                        kind="mandate",
                        headline=(
                            f"{portfolio_id} is within every {mandate_code} asset-class "
                            f"band at {LATEST_SNAPSHOT}."
                        ),
                        detail=(
                            f"All {len(bands)} bands checked against "
                            f"{total:,.0f} of portfolio base currency. Equity sits at "
                            f"{float(actual.get('Equity', 0.0)):.2f}%. Single-position "
                            f"limits are tested separately and may still bind."
                        ),
                        numbers={
                            "bands_checked": float(len(bands)),
                            "portfolio_total": total,
                            "equity_pct": float(actual.get("Equity", 0.0)),
                        },
                        sources=(
                            Source(
                                file="portfolios.csv",
                                row_ref=portfolio_id,
                                fields=("mandate_code", "service_model"),
                            ),
                        )
                        + tuple(
                            _mandate_source(mandate_code, str(b["asset_class"]))
                            for _, b in bands.iterrows()
                        ),
                        as_of=LATEST_SNAPSHOT,
                        confidence="derived",
                        severity=5,
                    )
                )

            # --- binding exclusions (sustainable mandates only) -------------
            notes = str(bands["mandate_notes"].iloc[0])
            if "exclusion" in notes.lower():
                for _, row in rows.iterrows():
                    instrument = instruments.loc[row["instrument_id"]]
                    if str(instrument.get("sustainability_excluded", "N")).upper() != "Y":
                        continue
                    weight = float(row["market_value_base"]) / total * 100
                    facts.append(
                        Fact(
                            fact_id=(
                                f"F-{client_id.replace('-', '')}-MANDATE-EXCLUSION-"
                                f"{portfolio_id}-{row['instrument_id']}"
                            ),
                            client_id=client_id,
                            kind="mandate",
                            headline=(
                                f"{portfolio_id} holds an excluded instrument at "
                                f"{weight:.2f}%: {row['instrument_name']}."
                            ),
                            detail=(
                                f"The {mandate_code} mandate carries binding "
                                f"exclusions and this instrument is flagged "
                                f"sustainability_excluded. Mandate notes: \"{notes}\""
                            ),
                            numbers={"weight_pct": weight},
                            quotes=(notes, str(row["instrument_name"])),
                            sources=(
                                _mandate_source(mandate_code, str(row["asset_class"])),
                                Source(
                                    file="instruments.csv",
                                    row_ref=str(row["instrument_id"]),
                                    fields=("sustainability_excluded",),
                                ),
                            ),
                            as_of=LATEST_SNAPSHOT,
                            confidence="verified",
                            severity=90,
                        )
                    )
    return facts
