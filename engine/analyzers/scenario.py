"""Adapter: scenario impacts -> Fact, marked as hypothesis.

engine/scenario.py shocks a book under the Hormuz escalate and reopen cases and
returns dataclasses. Those cannot reach the vault directly: forward-looking
analysis has to arrive as a Fact carrying confidence="scenario", because that
is what routes it into Scenario/ and what the model refuses to let masquerade
as settled.

Every fact here says, in its own text, that it describes something that has not
happened. The folder boundary and the visual treatment both matter, but neither
is a substitute for the sentence saying so.
"""

from __future__ import annotations

from engine import scenario as engine_scenario
from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

SCENARIOS: tuple[str, ...] = ("hormuz_escalate", "hormuz_reopen")

# The scenario labels are our own words, not a quotation from any row, and they
# carry numbers - a Brent level. Those are declared here as stated assumptions
# rather than smuggled in as a quote, because a quote exempts its numerals from
# the claim check and this text has no row to be checked against.
SCENARIO_ASSUMPTIONS: dict[str, dict[str, float]] = {
    "hormuz_escalate": {"assumed_brent_usd": 100.0},
    "hormuz_reopen": {"assumed_brent_usd": 75.0},
}

# The dated events these scenarios extend. Citing them anchors a hypothesis to
# something that actually happened, which is the only honest way to build one.
SCENARIO_EVENTS: tuple[str, ...] = ("2026-03-04", "2026-08-05")
# Below this the shock is not worth a note of its own.
MIN_ABS_PCT = 0.5


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0002",)) -> list[Fact]:
    facts: list[Fact] = []

    for name in SCENARIOS:
        for summary in engine_scenario.compute(dataset, name):  # type: ignore[arg-type]
            if summary.client_id not in client_ids:
                continue
            if abs(summary.total_impact_pct) < MIN_ABS_PCT and not summary.collateral_flags:
                continue

            client_id = summary.client_id
            direction = "loses" if summary.total_impact_usd < 0 else "gains"
            movers = (summary.top_losers or [])[:2] + (summary.top_gainers or [])[:1]

            # Cite the holdings the shock was applied to, and the instrument rows
            # whose sector and region decided which shock rule matched.
            cited: list[Source] = []
            for impact in movers:
                for portfolio in _portfolios_holding(
                    dataset, client_id, impact["instrument_id"]
                ):
                    cited.append(
                        Source(
                            file="holdings.csv",
                            row_ref=f"{portfolio}|{impact['instrument_id']}|{LATEST_SNAPSHOT}",
                            fields=("market_value_usd",),
                        )
                    )
                cited.append(
                    Source(
                        file="instruments.csv",
                        row_ref=str(impact["instrument_id"]),
                        fields=("asset_class", "sector", "region"),
                    )
                )
            if not cited:
                continue

            worst = movers[0] if movers else None
            worst_line = (
                f" The largest single move is {worst['instrument_name']} at "
                f"{worst['impact_usd']:,.0f}, from a {worst['shock_pct']:.2f}% shock "
                f"applied because {worst['shock_rule']}."
                if worst
                else ""
            )
            collateral_line = (
                f" {len(summary.collateral_flags)} facility would come under pressure "
                f"at these levels."
                if summary.collateral_flags
                else " No facility crosses its trigger at these levels."
            )

            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-SCENARIO-{name.upper().replace('_', '-')}",
                    client_id=client_id,
                    kind="scenario",
                    headline=(
                        f"If this happens — {summary.scenario_label} — the book "
                        f"{direction} {abs(summary.total_impact_usd):,.0f}, "
                        f"{abs(summary.total_impact_pct):.2f}% of "
                        f"{summary.current_portfolio_usd:,.0f}."
                    ),
                    detail=(
                        f"This has not happened. It is a shock applied to today's "
                        f"holdings under stated rules, not a forecast and not a "
                        f"statement about the past.{worst_line}{collateral_line} "
                        f"Shocked value would be "
                        f"{summary.shocked_portfolio_usd:,.0f}."
                    ),
                    numbers={
                        "impact_usd": abs(summary.total_impact_usd),
                        "impact_pct": abs(summary.total_impact_pct),
                        "current_usd": summary.current_portfolio_usd,
                        "shocked_usd": summary.shocked_portfolio_usd,
                        "facilities_flagged": float(len(summary.collateral_flags)),
                        **SCENARIO_ASSUMPTIONS.get(name, {}),
                        **(
                            {
                                "worst_impact_usd": float(worst["impact_usd"]),
                                "worst_shock_pct": float(worst["shock_pct"]),
                            }
                            if worst
                            else {}
                        ),
                    },
                    # instrument_name is verbatim from a cited row; the label
                    # and the shock rule are ours, so they are not claimed as
                    # quotations and their numerals are declared above instead.
                    quotes=((str(worst["instrument_name"]),) if worst else ()),
                    sources=tuple(dict.fromkeys(cited))
                    + tuple(
                        Source(
                            file="event_log.csv",
                            row_ref=event_date,
                            fields=("description", "primary_transmission", "severity"),
                        )
                        for event_date in SCENARIO_EVENTS
                    ),
                    as_of=LATEST_SNAPSHOT,
                    confidence="scenario",
                    severity=min(100, int(abs(summary.total_impact_pct) * 4) + 20),
                )
            )
    return facts


def _portfolios_holding(dataset: Dataset, client_id: str, instrument_id: str) -> list[str]:
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)
    match = holdings[
        (holdings["client_id"] == client_id)
        & (holdings["instrument_id"] == instrument_id)
    ]
    return [str(p) for p in match["portfolio_id"].unique()]
