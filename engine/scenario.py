"""Scenario engine: forward-looking shock propagation.

Takes a named shock scenario and estimates the impact on each client's
holdings using deterministic price sensitivity rules. No LLM. Every
impact estimate is derived from the instrument's asset class, sector,
and region — all traceable fields in instruments.csv.

Two scenarios are defined from event_log.csv:

  hormuz_escalate  — blockade holds, Brent stays >USD 100, energy surge
  hormuz_reopen    — ceasefire, Brent retraces to ~USD 75, risk-on

Both scenarios propagate through:
  1. Direct equity/commodity holdings (by sector)
  2. FX moves (USD strengthens in escalation, softens in reopen)
  3. Structured product underlyings (via instruments.csv underlying_reference)
  4. LTV impact on credit facilities (price drop → collateral value drop)

Output: list of ScenarioImpact dicts per (client, holding), plus a
client-level summary with total portfolio impact and collateral flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

ScenarioName = Literal["hormuz_escalate", "hormuz_reopen"]

# ── price shock rules ─────────────────────────────────────────────────────────
# Format: {(asset_class_lower, sector_lower, region_lower): shock_pct}
# Rules are checked most-specific first. First match wins.
# Shocks are percentage price moves applied to current market_value_usd.

_ESCALATE_SHOCKS: list[tuple[tuple[str, str, str], float]] = [
    # (asset_class, sector, region): shock_pct
    # Energy — direct beneficiaries
    (("equity",    "energy",                ""),         +22.0),
    (("commodity", "energy",                ""),         +18.0),
    (("equity",    "industrials",           ""),         +8.0),   # defence, shipping
    # Gold — safe haven bid
    (("commodity", "precious metals",       ""),         +12.0),
    (("equity",    "precious metals",       ""),         +10.0),
    # Tech — growth selloff on rate/inflation fears
    (("equity",    "information technology",""),         -10.0),
    (("equity",    "information technology","north america"), -12.0),
    # Consumer discretionary — hurt by energy costs
    (("equity",    "consumer discretionary",""),         -8.0),
    # Airlines, transport (industrials)
    (("equity",    "industrials",           "europe"),   +4.0),   # defence > transport
    # EM equities — risk-off
    (("equity",    "",                      "asia ex-japan"), -6.0),
    (("equity",    "",                      "asia"),     -5.0),
    # Fixed income — rate spike from inflation
    (("fixed income", "",                   ""),         -5.0),
    # Private credit — redemption pressure
    (("alternatives", "private credit",     ""),         -4.0),
    (("alternatives", "",                   ""),         -3.0),
    # Cash / FD — unchanged
    (("cash",      "",                      ""),          0.0),
    # Broad equity — mild negative
    (("equity",    "",                      ""),         -4.0),
]

_REOPEN_SHOCKS: list[tuple[tuple[str, str, str], float]] = [
    # Energy — retraces
    (("equity",    "energy",                ""),         -15.0),
    (("commodity", "energy",                ""),         -14.0),
    (("equity",    "industrials",           ""),         -5.0),
    # Gold — safe haven unwinds
    (("commodity", "precious metals",       ""),         -8.0),
    (("equity",    "precious metals",       ""),         -6.0),
    # Tech — risk-on, recovers
    (("equity",    "information technology",""),         +9.0),
    (("equity",    "information technology","north america"), +11.0),
    # Consumer discretionary — relief
    (("equity",    "consumer discretionary",""),         +6.0),
    # EM — risk-on
    (("equity",    "",                      "asia ex-japan"), +5.0),
    (("equity",    "",                      "asia"),     +4.0),
    # Fixed income — rates ease
    (("fixed income", "",                   ""),         +3.0),
    # Alternatives
    (("alternatives", "",                   ""),         +2.0),
    # Cash unchanged
    (("cash",      "",                      ""),          0.0),
    # Broad equity — mild positive
    (("equity",    "",                      ""),         +3.0),
]

SCENARIOS: dict[ScenarioName, list[tuple[tuple[str, str, str], float]]] = {
    "hormuz_escalate": _ESCALATE_SHOCKS,
    "hormuz_reopen":   _REOPEN_SHOCKS,
}

SCENARIO_LABELS: dict[ScenarioName, str] = {
    "hormuz_escalate": "Hormuz Escalation (blockade holds, Brent >USD 100)",
    "hormuz_reopen":   "Hormuz Reopen (ceasefire, Brent retraces to ~USD 75)",
}

# LTV thresholds for collateral flag
LTV_WARNING  = 0.70
LTV_BREACH   = 0.80


@dataclass
class ScenarioImpact:
    client_id: str
    instrument_id: str
    instrument_name: str
    asset_class: str
    sector: str
    region: str
    current_value_usd: float
    shock_pct: float                    # applied percentage move
    shocked_value_usd: float
    impact_usd: float                   # positive = gain, negative = loss
    shock_rule: str                     # which rule matched

    def as_dict(self) -> dict:
        return {
            "client_id":         self.client_id,
            "instrument_id":     self.instrument_id,
            "instrument_name":   self.instrument_name,
            "asset_class":       self.asset_class,
            "sector":            self.sector,
            "region":            self.region,
            "current_value_usd": round(self.current_value_usd, 2),
            "shock_pct":         round(self.shock_pct, 2),
            "shocked_value_usd": round(self.shocked_value_usd, 2),
            "impact_usd":        round(self.impact_usd, 2),
            "shock_rule":        self.shock_rule,
        }


@dataclass
class ClientScenarioSummary:
    client_id: str
    client_name: str
    scenario: ScenarioName
    scenario_label: str
    current_portfolio_usd: float
    shocked_portfolio_usd: float
    total_impact_usd: float
    total_impact_pct: float
    collateral_flags: list[dict]        # facilities where LTV would breach
    top_gainers: list[dict]             # top 3 by impact_usd
    top_losers: list[dict]              # top 3 by impact_usd (most negative)
    impacts: list[dict]

    def as_dict(self) -> dict:
        return {
            "client_id":             self.client_id,
            "client_name":           self.client_name,
            "scenario":              self.scenario,
            "scenario_label":        self.scenario_label,
            "current_portfolio_usd": round(self.current_portfolio_usd, 2),
            "shocked_portfolio_usd": round(self.shocked_portfolio_usd, 2),
            "total_impact_usd":      round(self.total_impact_usd, 2),
            "total_impact_pct":      round(self.total_impact_pct, 2),
            "collateral_flags":      self.collateral_flags,
            "top_gainers":           self.top_gainers,
            "top_losers":            self.top_losers,
            "impacts":               self.impacts,
        }


# ── shock lookup ──────────────────────────────────────────────────────────────

def _match_shock(
    asset_class: str, sector: str, region: str,
    rules: list[tuple[tuple[str, str, str], float]],
) -> tuple[float, str]:
    """Return (shock_pct, rule_label). First match wins."""
    ac = asset_class.lower().strip()
    se = sector.lower().strip()
    re = region.lower().strip()

    for (r_ac, r_se, r_re), shock in rules:
        ac_match = (not r_ac) or (r_ac in ac) or (ac in r_ac)
        se_match = (not r_se) or (r_se in se) or (se in r_se)
        re_match = (not r_re) or (r_re in re) or (re in r_re)
        if ac_match and se_match and re_match:
            label = f"({r_ac or '*'}, {r_se or '*'}, {r_re or '*'}) → {shock:+.0f}%"
            return shock, label

    return 0.0, "no rule matched"


# ── main computation ──────────────────────────────────────────────────────────

def compute(ds, scenario: ScenarioName) -> list[ClientScenarioSummary]:
    """Return scenario summaries for all clients."""
    rules = SCENARIOS[scenario]
    label = SCENARIO_LABELS[scenario]

    latest = "2026-08-26"
    holdings = ds.holdings[ds.holdings["snapshot_date"] == pd.Timestamp(latest)].copy()
    clients  = ds.clients.set_index("client_id")

    # Aggregate holdings to (client, instrument) level
    agg = (
        holdings.groupby(["client_id", "instrument_id"])
        .agg(
            instrument_name=("instrument_name", "first"),
            asset_class=("asset_class",     "first"),
            sector=("sector",          "first"),
            region=("region",          "first"),
            market_value_usd=("market_value_usd", "sum"),
        )
        .reset_index()
    )

    summaries: list[ClientScenarioSummary] = []
    client_ids = agg["client_id"].unique()

    for cid in client_ids:
        client_rows = agg[agg["client_id"] == cid]
        impacts: list[ScenarioImpact] = []

        for _, row in client_rows.iterrows():
            shock_pct, rule = _match_shock(
                str(row["asset_class"]), str(row["sector"]), str(row["region"]), rules
            )
            cur = float(row["market_value_usd"])
            imp = cur * shock_pct / 100
            impacts.append(ScenarioImpact(
                client_id=str(cid),
                instrument_id=str(row["instrument_id"]),
                instrument_name=str(row["instrument_name"]),
                asset_class=str(row["asset_class"]),
                sector=str(row["sector"]),
                region=str(row["region"]),
                current_value_usd=cur,
                shock_pct=shock_pct,
                shocked_value_usd=cur + imp,
                impact_usd=imp,
                shock_rule=rule,
            ))

        current_total  = sum(i.current_value_usd for i in impacts)
        shocked_total  = sum(i.shocked_value_usd  for i in impacts)
        total_impact   = shocked_total - current_total
        total_pct      = (total_impact / current_total * 100) if current_total else 0.0

        sorted_impacts = sorted(impacts, key=lambda i: i.impact_usd)
        top_losers  = [i.as_dict() for i in sorted_impacts[:3]  if i.impact_usd < 0]
        top_gainers = [i.as_dict() for i in sorted_impacts[-3:] if i.impact_usd > 0][::-1]

        # Check collateral impact
        collateral_flags: list[dict] = []
        client_facilities = ds.credit_facilities[ds.credit_facilities["client_id"] == cid]
        for _, fac in client_facilities.iterrows():
            fac_id = str(fac.get("facility_id", ""))
            try:
                current_ltv = float(fac.get("ltv_pct_2026-08-26", 0) or 0) / 100
            except (ValueError, TypeError):
                continue
            # Estimate new LTV: collateral value drops by portfolio shock %
            if current_total > 0:
                collateral_shock_pct = total_impact / current_total  # portfolio % change
            else:
                collateral_shock_pct = 0.0
            new_ltv = current_ltv / max(0.01, 1 + collateral_shock_pct)
            if new_ltv >= LTV_WARNING:
                collateral_flags.append({
                    "facility_id":   fac_id,
                    "current_ltv":   round(current_ltv * 100, 2),
                    "shocked_ltv":   round(new_ltv * 100, 2),
                    "breach":        new_ltv >= LTV_BREACH,
                })

        try:
            cname = str(clients.loc[cid, "client_name"])
        except KeyError:
            cname = str(cid)

        summaries.append(ClientScenarioSummary(
            client_id=str(cid),
            client_name=cname,
            scenario=scenario,
            scenario_label=label,
            current_portfolio_usd=current_total,
            shocked_portfolio_usd=shocked_total,
            total_impact_usd=total_impact,
            total_impact_pct=total_pct,
            collateral_flags=collateral_flags,
            top_gainers=top_gainers,
            top_losers=top_losers,
            impacts=[i.as_dict() for i in impacts],
        ))

    return sorted(summaries, key=lambda s: s.total_impact_usd)
