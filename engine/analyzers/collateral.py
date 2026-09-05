"""Lombard collateral: LTV path, breaches, and *why*.

LTV is recomputed as drawn / lending_value rather than read from the shipped
ltv_pct_<date> column, so the fact stands on the inputs.

The part that matters is attribution. A breach caused by the market is a
conversation about markets; a breach caused by the client drawing more, days
after being warned, is a completely different conversation. The classifier is a
counterfactual: hold `drawn` fixed and see what the collateral did, hold
`lending_value` fixed and see what the drawdown did, attribute to whichever
dominates. Without it an analyzer will reach for the nearest market event and
be confidently wrong - which is exactly what happened to the first draft of the
project plan for CF-0001.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.loader import SNAPSHOTS, Dataset
from engine.models import Fact, Source

MARKET_DRIVEN = "market move"
CLIENT_DRIVEN = "client action"


@dataclass(frozen=True)
class Point:
    snapshot: str
    drawn: float
    lending_value: float

    @property
    def ltv(self) -> float:
        return self.drawn / self.lending_value * 100


@dataclass(frozen=True)
class Step:
    frm: Point
    to: Point
    draw_effect_pp: float
    market_effect_pp: float

    @property
    def driver(self) -> str:
        return (
            CLIENT_DRIVEN
            if abs(self.draw_effect_pp) > abs(self.market_effect_pp)
            else MARKET_DRIVEN
        )

    @property
    def change_pp(self) -> float:
        return self.to.ltv - self.frm.ltv


def ltv_path(facility: pd.Series) -> list[Point]:
    return [
        Point(
            snapshot=snapshot,
            drawn=float(facility[f"drawn_{snapshot}"]),
            lending_value=float(facility[f"lending_value_{snapshot}"]),
        )
        for snapshot in SNAPSHOTS
    ]


def steps(path: list[Point]) -> list[Step]:
    """Counterfactual attribution for each move along the path."""
    out: list[Step] = []
    for frm, to in zip(path, path[1:], strict=False):
        ltv_if_only_draw = to.drawn / frm.lending_value * 100
        ltv_if_only_market = frm.drawn / to.lending_value * 100
        out.append(
            Step(
                frm=frm,
                to=to,
                draw_effect_pp=ltv_if_only_draw - frm.ltv,
                market_effect_pp=ltv_if_only_market - frm.ltv,
            )
        )
    return out


def _facility_source(facility_id: str, fields: tuple[str, ...]) -> Source:
    return Source(file="credit_facilities.csv", row_ref=facility_id, fields=fields)


def run(dataset: Dataset, client_ids: tuple[str, ...] = ("CL-0002",)) -> list[Fact]:
    facts: list[Fact] = []
    facilities = dataset.credit_facilities
    facilities = facilities[facilities["client_id"].isin(client_ids)]

    for _, facility in facilities.iterrows():
        facility_id = str(facility["facility_id"])
        client_id = str(facility["client_id"])
        collateral = str(facility["collateral_portfolio_id"])
        trigger = float(facility["margin_call_ltv_pct"])
        path = ltv_path(facility)
        moves = steps(path)
        latest = path[-1]

        breaches = [point for point in path if point.ltv >= trigger]
        series_fields = tuple(
            f"{prefix}_{snapshot}"
            for snapshot in SNAPSHOTS
            for prefix in ("drawn", "lending_value")
        )
        path_source = _facility_source(
            facility_id, ("margin_call_ltv_pct",) + series_fields
        )

        if breaches:
            worst = max(breaches, key=lambda point: point.ltv)
            step = next((s for s in moves if s.to.snapshot == worst.snapshot), None)

            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-COLLAT-BREACH",
                    client_id=client_id,
                    kind="collateral",
                    headline=(
                        f"{facility_id} breached its margin-call trigger at "
                        f"{worst.snapshot}: LTV {worst.ltv:.2f}% against a "
                        f"{trigger:.2f}% trigger."
                    ),
                    detail=(
                        f"Recomputed as drawn / lending value, not read from the "
                        f"reported LTV column. At that date {worst.drawn:,.0f} was "
                        f"drawn against {worst.lending_value:,.0f} of lending value "
                        f"on {collateral}."
                    ),
                    numbers={
                        "ltv_pct": worst.ltv,
                        "trigger_pct": trigger,
                        "drawn": worst.drawn,
                        "lending_value": worst.lending_value,
                    },
                    sources=(path_source,),
                    as_of=worst.snapshot,
                    confidence="derived",
                    severity=95,
                )
            )

            if step is not None:
                drawn_change = step.to.drawn - step.frm.drawn
                lending_change = step.to.lending_value - step.frm.lending_value
                if step.driver == CLIENT_DRIVEN:
                    verdict = (
                        f"This was {CLIENT_DRIVEN}, not a market move. Holding "
                        f"collateral fixed, the drawdown alone moved LTV by "
                        f"{step.draw_effect_pp:+.2f} points; holding the drawdown "
                        f"fixed, the collateral alone moved it "
                        f"{step.market_effect_pp:+.2f} points — the market was "
                        f"pushing LTV down over this window."
                    )
                else:
                    verdict = (
                        f"This was a {MARKET_DRIVEN}. Holding the drawdown fixed, "
                        f"collateral alone moved LTV {step.market_effect_pp:+.2f} "
                        f"points against {step.draw_effect_pp:+.2f} points from "
                        f"drawing."
                    )

                facts.append(
                    Fact(
                        fact_id=f"F-{client_id.replace('-', '')}-COLLAT-DRIVER",
                        client_id=client_id,
                        kind="collateral",
                        headline=(
                            f"The {facility_id} breach was {step.driver}: LTV moved "
                            f"{step.change_pp:+.2f} points between {step.frm.snapshot} "
                            f"and {step.to.snapshot}."
                        ),
                        detail=(
                            f"{verdict} Drawn changed by {drawn_change:+,.0f} and "
                            f"lending value by {lending_change:+,.0f} over the same "
                            f"window."
                        ),
                        numbers={
                            "change_pp": step.change_pp,
                            "draw_effect_pp": step.draw_effect_pp,
                            "market_effect_pp": step.market_effect_pp,
                            "drawn_change": drawn_change,
                            "lending_change": lending_change,
                        },
                        sources=(path_source,),
                        as_of=step.to.snapshot,
                        confidence="derived",
                        severity=90,
                    )
                )

        # --- cures: the mirror image, and the more interesting one ---------
        # A facility that leaves breach because the market moved was not fixed
        # by anybody. That distinction changes what the RM should say next.
        for step in moves:
            if not (step.frm.ltv >= trigger > step.to.ltv):
                continue
            drawn_change = step.to.drawn - step.frm.drawn
            lending_change = step.to.lending_value - step.frm.lending_value
            if step.driver == MARKET_DRIVEN:
                verdict = (
                    f"Nothing was done: drawn was unchanged at "
                    f"{step.to.drawn:,.0f} across the window and the collateral "
                    f"revalued upward by {lending_change:,.0f}. The facility was "
                    f"cured by an event, not by an action."
                )
            else:
                verdict = (
                    f"The position was actively reduced: drawn moved "
                    f"{drawn_change:+,.0f} over the window."
                )
            facts.append(
                Fact(
                    fact_id=f"F-{client_id.replace('-', '')}-COLLAT-CURE",
                    client_id=client_id,
                    kind="collateral",
                    headline=(
                        f"{facility_id} came back under its {trigger:.2f}% trigger "
                        f"at {step.to.snapshot} by {step.driver}: LTV "
                        f"{step.frm.ltv:.2f}% to {step.to.ltv:.2f}%."
                    ),
                    detail=(
                        f"{verdict} Holding drawn fixed, the collateral alone moved "
                        f"LTV {step.market_effect_pp:+.2f} points; holding collateral "
                        f"fixed, drawing alone moved it {step.draw_effect_pp:+.2f} "
                        f"points."
                    ),
                    numbers={
                        "ltv_before_pct": step.frm.ltv,
                        "ltv_after_pct": step.to.ltv,
                        "trigger_pct": trigger,
                        "drawn_after": step.to.drawn,
                        "drawn_change": drawn_change,
                        "lending_change": lending_change,
                        "market_effect_pp": step.market_effect_pp,
                        "draw_effect_pp": step.draw_effect_pp,
                    },
                    sources=(path_source,),
                    as_of=step.to.snapshot,
                    confidence="derived",
                    severity=60,
                )
            )

        headroom = latest.lending_value - latest.drawn
        distance_pp = trigger - latest.ltv
        facts.append(
            Fact(
                fact_id=f"F-{client_id.replace('-', '')}-COLLAT-CURRENT",
                client_id=client_id,
                kind="collateral",
                headline=(
                    f"{facility_id} currently sits at {latest.ltv:.2f}% LTV, "
                    f"{distance_pp:.2f} points below its {trigger:.2f}% trigger."
                ),
                detail=(
                    f"Headroom of {headroom:,.0f} in lending value at "
                    f"{latest.snapshot}, secured on {collateral}."
                ),
                numbers={
                    "ltv_pct": latest.ltv,
                    "trigger_pct": trigger,
                    "distance_pp": distance_pp,
                    "headroom": headroom,
                },
                sources=(path_source,),
                as_of=latest.snapshot,
                confidence="derived",
                severity=max(0, min(100, int(100 - distance_pp * 4))),
            )
        )
    return facts
