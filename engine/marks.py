"""Derived marks: a real factor move applied to a synthetic instrument.

The chain is deliberately short and every link is inspectable.

    last snapshot price           instruments.csv, price_<snapshot>
      x (1 + factor move x beta)  live quote + market_sensitivities.yaml
    = derived mark

A mark is not a price. Nothing here observes a trade, because the instruments
are invented and no trade exists to observe. What a mark asserts is: *this is
what the last known price becomes if the named real factor moved as it actually
did and the published sensitivity holds.* Both halves of that - the factor move
and the sensitivity - are cited on every fact that uses the result, so the
estimate travels with its workings rather than arriving as a bare number.

Two modes, because bonds and equities respond to different things. An equity
scales with a factor's percentage change. A bond responds to the *absolute*
change in a yield, times duration, with a sign flip. Treating a yield series
like a price series is a sign error that looks like a small innocuous number,
which is exactly why it is worth separating.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from engine.live_feed import Quote
from engine.loader import LATEST_SNAPSHOT, Dataset

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "market_sensitivities.yaml"

ANY = "*"
MATCHABLE = ("asset_class", "sub_asset_class", "sector", "region")

RELATIVE = "relative"
DURATION = "duration"
NONE = "none"
MODES = (RELATIVE, DURATION, NONE)


class SensitivityConfigError(RuntimeError):
    """The sensitivity map is malformed. Refuse rather than mark wrongly."""


@dataclass(frozen=True)
class Mark:
    """One instrument's derived mark, with everything needed to defend it."""

    instrument_id: str
    base_price: float          # the last snapshot price this started from
    price: float               # the derived mark
    rule_id: str
    mode: str
    factor: str | None         # series_id, or None when nothing was applied
    coefficient: float         # beta, or duration_years in DURATION mode
    factor_move: float | None  # percent for RELATIVE, points for DURATION
    price_pct: float           # what was applied to base_price
    live: bool                 # was the factor actually quoted live
    why: str                   # the rule's own reasoning, verbatim

    @property
    def moved(self) -> bool:
        return abs(self.price_pct) > 1e-12


def load_sensitivities(path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text())
    if "rules" not in config or "defaults" not in config:
        raise SensitivityConfigError(
            f"{path.name} needs both a 'defaults' and a 'rules' section"
        )
    seen: set[str] = set()
    for rule in config["rules"]:
        rule_id = rule.get("id")
        if not rule_id:
            raise SensitivityConfigError(f"a rule has no id: {rule}")
        if rule_id in seen:
            raise SensitivityConfigError(f"duplicate rule id {rule_id!r}")
        seen.add(rule_id)
        if rule.get("mode") not in MODES:
            raise SensitivityConfigError(
                f"{rule_id}: mode must be one of {MODES}, got {rule.get('mode')!r}"
            )
        if not str(rule.get("why", "")).strip():
            raise SensitivityConfigError(
                f"{rule_id}: every rule needs a 'why'. A beta whose reasoning is "
                f"not written down cannot be argued with, and being arguable is "
                f"the only thing that makes it legitimate."
            )
        if rule["mode"] == RELATIVE and "factor" not in rule:
            raise SensitivityConfigError(f"{rule_id}: relative mode needs a factor")
        if rule["mode"] == DURATION and "duration_years" not in rule:
            raise SensitivityConfigError(
                f"{rule_id}: duration mode needs duration_years"
            )
        for field in rule.get("match", {}):
            if field not in MATCHABLE:
                raise SensitivityConfigError(
                    f"{rule_id}: cannot match on {field!r}; "
                    f"instruments.csv offers {MATCHABLE}"
                )
    return config


def match_rule(instrument: dict, config: dict) -> dict:
    """The first rule whose every stated field matches. Order is the priority."""
    for rule in config["rules"]:
        criteria = rule.get("match", {})
        if all(
            criteria[field] == ANY
            or str(instrument.get(field, "")).strip() == criteria[field]
            for field in criteria
        ):
            return rule
    return {"id": "default", **config["defaults"], "match": {}}


def _price_pct(rule: dict, quote: Quote | None) -> tuple[float, float | None, float]:
    """(price_pct, factor_move, coefficient) for one rule against one quote."""
    mode = rule["mode"]
    if mode == NONE:
        return 0.0, None, float(rule.get("beta", 0.0))

    coefficient = float(
        rule.get("duration_years" if mode == DURATION else "beta", 0.0)
    )

    if quote is None or not quote.live:
        # The rule wanted a factor and the factor is not live. Hold the mark;
        # never reach for the snapshot value and call the difference a move.
        return 0.0, None, coefficient

    if mode == RELATIVE:
        move = quote.pct_change
        if move is None:
            return 0.0, None, coefficient
        return move * coefficient, move, coefficient

    # DURATION: a yield series. The move that matters is the change in POINTS,
    # and a falling yield raises a bond's price, hence the sign flip.
    if quote.previous_close is None:
        return 0.0, None, coefficient
    points = quote.value - quote.previous_close
    return -coefficient * points, points, coefficient


def derive_marks(
    dataset: Dataset,
    quotes: dict[str, Quote],
    config: dict | None = None,
    snapshot: str = LATEST_SNAPSHOT,
) -> dict[str, Mark]:
    """A mark for every instrument in the dataset.

    Instruments whose rule is `none`, or whose factor was not quoted live, come
    back with price == base_price and `moved` False. They are returned rather
    than omitted, so a caller can always distinguish "this did not move" from
    "this was never considered".
    """
    config = config or load_sensitivities()
    price_column = f"price_{snapshot}"
    instruments = dataset.instruments

    if price_column not in instruments.columns:
        raise SensitivityConfigError(
            f"instruments.csv has no {price_column!r}; cannot mark from a "
            f"snapshot that is not there"
        )

    marks: dict[str, Mark] = {}
    for _, row in instruments.iterrows():
        instrument = {field: row.get(field) for field in MATCHABLE}
        rule = match_rule(instrument, config)
        factor = rule.get("factor")
        quote = quotes.get(factor) if factor else None

        price_pct, factor_move, coefficient = _price_pct(rule, quote)
        try:
            base_price = float(row[price_column])
        except (TypeError, ValueError):
            continue

        marks[str(row["instrument_id"])] = Mark(
            instrument_id=str(row["instrument_id"]),
            base_price=base_price,
            price=base_price * (1 + price_pct / 100),
            rule_id=str(rule["id"]),
            mode=str(rule["mode"]),
            factor=str(factor) if factor else None,
            coefficient=coefficient,
            factor_move=factor_move,
            price_pct=price_pct,
            live=bool(quote.live) if quote else False,
            why=str(rule.get("why", "")).strip(),
        )
    return marks


def revalue_holdings(
    dataset: Dataset,
    marks: dict[str, Mark],
    snapshot: str = LATEST_SNAPSHOT,
):
    """Holdings at the latest snapshot, with market and lending value re-marked.

    Quantity is held fixed and the price replaced - nobody traded, the mark
    moved. lending_value_base is rescaled by the same ratio, because the advance
    rate against a position does not change when its price does.

    Returns a copy. data/ is never written to, and neither is the loaded frame.
    """
    holdings = dataset.holdings_at(snapshot).copy()
    if holdings.empty:
        return holdings

    ratio = holdings["instrument_id"].map(
        lambda iid: (marks[iid].price / marks[iid].base_price)
        if iid in marks and marks[iid].base_price
        else 1.0
    )
    holdings["mark_ratio"] = ratio
    for column in ("market_value_base", "market_value_usd", "lending_value_base"):
        if column in holdings.columns:
            holdings[column] = holdings[column] * ratio
    return holdings
