"""Who needs a call first thing on Monday.

Twenty clients, one relationship manager. This ranks the whole book on signals
cheap enough to compute for every client, not just the ones we have gone deep
on - which is the point: the shallow scan is what finds the client nobody was
looking at.

The score is an ordering device, not a probability. Every weight lives in
engine/config/triage_weights.yaml with its reasoning written next to it, and is
surfaced in the UI, so the ranking can be argued with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from engine.analyzers import tension as tension_module
from engine.analyzers.lookthrough import exposures_by_portfolio, exposures_for_client
from engine.loader import LATEST_SNAPSHOT, Dataset
from engine.models import Fact, Source

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "triage_weights.yaml"
CUSTODY = "Custody"


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text())
    total = sum(config["weights"].values())
    if total != 100:
        raise ValueError(f"triage weights must sum to 100, got {total}")
    return config


@dataclass
class Score:
    client_id: str
    client_name: str
    signals: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    evidence: list[Source] = field(default_factory=list)

    def top_signals(self, n: int = 3) -> list[tuple[str, float]]:
        return sorted(self.signals.items(), key=lambda kv: -kv[1])[:n]


def rank_book(dataset: Dataset, config: dict | None = None) -> list[Score]:
    """Score and order every client. Cheap enough to run over the whole book."""
    config = config or load_config()
    weights = config["weights"]
    params = config["parameters"]

    as_of = pd.Timestamp(LATEST_SNAPSHOT)
    holdings = dataset.holdings_at(LATEST_SNAPSHOT)
    fx = dataset.fx_at(LATEST_SNAPSHOT)

    tension_by_client: dict[str, list[Fact]] = {}
    for fact in tension_module.run(dataset, tuple(dataset.clients["client_id"])):
        tension_by_client.setdefault(fact.client_id, []).append(fact)

    scores: list[Score] = []
    for _, client in dataset.clients.iterrows():
        client_id = str(client["client_id"])
        own = holdings[holdings["client_id"] == client_id]
        if own.empty:
            continue
        book = float(own["market_value_usd"].sum())
        evidence: list[Source] = []

        # -- collateral: distance to the nearest margin-call trigger ---------
        facilities = dataset.credit_facilities
        facilities = facilities[facilities["client_id"] == client_id]
        headroom = None
        for _, facility in facilities.iterrows():
            ltv = (
                float(facility[f"drawn_{LATEST_SNAPSHOT}"])
                / float(facility[f"lending_value_{LATEST_SNAPSHOT}"])
                * 100
            )
            distance = float(facility["margin_call_ltv_pct"]) - ltv
            headroom = distance if headroom is None else min(headroom, distance)
            evidence.append(
                Source(
                    file="credit_facilities.csv",
                    row_ref=str(facility["facility_id"]),
                    fields=("margin_call_ltv_pct", f"drawn_{LATEST_SNAPSHOT}",
                            f"lending_value_{LATEST_SNAPSHOT}"),
                )
            )
        span = float(params["collateral_headroom_pp"])
        if headroom is None:
            collateral = 0.0
        elif headroom <= 0:
            collateral = 100.0
        else:
            collateral = 100 * max(0.0, 1 - headroom / span) ** 2

        # -- liquidity: promised outflows vs unencumbered daily assets -------
        pledged = set(facilities["collateral_portfolio_id"])
        unencumbered = float(
            own[(own["liquidity_tier"] == "Daily")
                & (~own["portfolio_id"].isin(pledged))]["market_value_usd"].sum()
        )
        promised = 0.0
        needs = dataset.planned_cash_needs
        for _, need in needs[needs["client_id"] == client_id].iterrows():
            factor = (
                float(params["conditional_need_factor"])
                if "Conditional" in str(need["certainty"])
                else 1.0
            )
            promised += fx.to_usd(float(need["amount"]), str(need["currency"])) * factor
            evidence.append(
                Source(file="planned_cash_needs.csv", row_ref=str(need["need_id"]),
                       fields=("amount", "currency", "certainty"))
            )
        commitments = dataset.commitments
        for _, commitment in commitments[commitments["client_id"] == client_id].iterrows():
            promised += fx.to_usd(
                float(commitment["uncalled"]), str(commitment["currency"])
            ) * float(params["uncalled_commitment_factor"])
            evidence.append(
                Source(file="commitments.csv", row_ref=str(commitment["commitment_id"]),
                       fields=("uncalled", "currency"))
            )
        liquidity = (
            0.0
            if promised <= 0
            else min(100.0, max(0.0, (promised - unencumbered) / promised) * 100)
        )

        # -- tension: strongest stated-vs-actual finding ---------------------
        client_tensions = tension_by_client.get(client_id, [])
        tension = float(max((f.severity for f in client_tensions), default=0))
        for fact in client_tensions:
            evidence.extend(fact.sources[:1])

        # -- concentration: largest name after look-through ------------------
        largest = max(
            (e.total_base for e in exposures_for_client(dataset, client_id).values()),
            default=0.0,
        )
        largest_pct = largest / book * 100
        floor = float(params["concentration_floor_pct"])
        concentration = min(
            100.0,
            max(0.0, (largest_pct - floor) / float(params["concentration_span_pct"]) * 100),
        )

        # -- mandate: single-position limits after look-through --------------
        breaches = 0
        managed = dataset.portfolios[
            (dataset.portfolios["client_id"] == client_id)
            & (dataset.portfolios["service_model"] != CUSTODY)
        ]
        by_portfolio = exposures_by_portfolio(dataset, client_id)
        for _, portfolio in managed.iterrows():
            bands = dataset.mandates[
                dataset.mandates["mandate_code"] == portfolio["mandate_code"]
            ]
            if bands.empty:
                continue
            limit = float(bands["max_single_position_pct"].iloc[0])
            total = float(
                holdings[holdings["portfolio_id"] == portfolio["portfolio_id"]][
                    "market_value_base"
                ].sum()
            )
            if total <= 0:
                continue
            for exposure in by_portfolio.get(str(portfolio["portfolio_id"]), {}).values():
                if exposure.total_base / total * 100 > limit:
                    breaches += 1
        mandate = min(100.0, breaches * 50.0)

        # -- hygiene ----------------------------------------------------------
        notes = dataset.rm_notes[dataset.rm_notes["client_id"] == client_id]
        days = int((as_of - notes["note_date"].max()).days) if len(notes) else 365
        contact = min(100.0, days / float(params["contact_saturation_days"]) * 100)
        kyc_due = client["kyc_review_due"]
        kyc = 100.0 if pd.notna(kyc_due) and kyc_due < as_of else 0.0

        signals = {
            "collateral": collateral,
            "liquidity": liquidity,
            "tension": tension,
            "concentration": concentration,
            "mandate": mandate,
            "contact": contact,
            "kyc": kyc,
        }
        total_score = sum(weights[k] * v for k, v in signals.items()) / 100
        scores.append(
            Score(
                client_id=client_id,
                client_name=str(client["client_name"]),
                signals=signals,
                total=total_score,
                evidence=evidence,
            )
        )

    scores.sort(key=lambda s: -s.total)
    return scores


def run(dataset: Dataset, client_ids: tuple[str, ...] = ()) -> list[Fact]:
    """One fact per deep client, explaining where they sit and why."""
    config = load_config()
    ranking = rank_book(dataset, config)
    positions = {score.client_id: index + 1 for index, score in enumerate(ranking)}

    facts: list[Fact] = []
    for score in ranking:
        if score.client_id not in client_ids:
            continue
        rank = positions[score.client_id]
        drivers = score.top_signals(3)
        driver_text = ", ".join(
            f"{name} {value:.0f}" for name, value in drivers if value > 0
        ) or "no signal above zero"
        facts.append(
            Fact(
                fact_id=f"F-{score.client_id.replace('-', '')}-TRIAGE",
                client_id=score.client_id,
                kind="triage",
                headline=(
                    f"Ranked {rank} of {len(ranking)} in the book this week, "
                    f"scoring {score.total:.1f} of 100."
                ),
                detail=(
                    f"Strongest signals: {driver_text}. The score is an ordering "
                    f"device, not a probability — weights are published in "
                    f"engine/config/triage_weights.yaml and shown in the console so "
                    f"the ranking can be argued with rather than taken on trust."
                ),
                numbers={
                    "rank": float(rank),
                    "book_size": float(len(ranking)),
                    "scale_max": 100.0,
                    "score": score.total,
                    **{f"signal_{k}": v for k, v in score.signals.items()},
                },
                sources=tuple(dict.fromkeys(score.evidence))
                or (
                    Source(file="clients.csv", row_ref=score.client_id,
                           fields=("client_id",)),
                ),
                as_of=LATEST_SNAPSHOT,
                confidence="derived",
                severity=min(100, int(score.total)),
            )
        )
    return facts
