"""Run every analyzer and emit build/facts.json.

The envelope carries the facts *and* the source rows they point at, so the
vault, the console and the MCP server all read one file and none of them needs
to touch data/ or re-derive anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Iterable

from engine.analyzers import (
    attribution,
    collateral,
    liquidity,
    lookthrough,
    mandate,
    scenario,
    tension,
    triage,
)
from engine.loader import BUILD_DIR, LATEST_SNAPSHOT, Dataset, load
from engine.models import Fact
from engine.sources import resolve, source_key, verify_quotes

SCHEMA_VERSION = 1

# Clients we have gone deep on. Depth over breadth is the plan.
CLIENTS: tuple[str, ...] = ("CL-0002", "CL-0001")

ANALYZERS: list[tuple[str, Callable[[Dataset], Iterable[Fact]]]] = [
    ("attribution", lambda ds: attribution.run(ds, CLIENTS)),
    ("lookthrough", lambda ds: lookthrough.run(ds, CLIENTS)),
    ("collateral", lambda ds: collateral.run(ds, CLIENTS)),
    ("tension", lambda ds: tension.run(ds, CLIENTS)),
    ("liquidity", lambda ds: liquidity.run(ds, CLIENTS)),
    ("mandate", lambda ds: mandate.run(ds, CLIENTS)),
    ("triage", lambda ds: triage.run(ds, CLIENTS)),
    ("scenario", lambda ds: scenario.run(ds, CLIENTS)),
]


class DuplicateFactId(RuntimeError):
    """Two facts share an id. They would silently overwrite each other."""


def collect(dataset: Dataset) -> list[Fact]:
    facts: list[Fact] = []
    for name, analyzer in ANALYZERS:
        produced = list(analyzer(dataset))
        print(f"  {name:<14} {len(produced):>4} facts")
        facts.extend(produced)

    # A fact_id is the key in the vault, the console and the MCP server. A
    # collision would not error anywhere downstream - it would just lose a fact.
    seen: dict[str, int] = {}
    for fact in facts:
        seen[fact.fact_id] = seen.get(fact.fact_id, 0) + 1
    clashes = {k: v for k, v in seen.items() if v > 1}
    if clashes:
        raise DuplicateFactId(f"duplicate fact ids: {clashes}")
    return facts


def source_rows(dataset: Dataset, facts: list[Fact]) -> dict[str, dict]:
    """Every row any fact cites, resolved once."""
    rows: dict[str, dict] = {}
    for fact in facts:
        for source in fact.sources:
            key = source_key(source.file, source.row_ref)
            if key not in rows:
                rows[key] = resolve(dataset, source.file, source.row_ref)
    return rows


def build() -> dict:
    dataset = load()
    print(f"loaded dataset; {len(ANALYZERS)} analyzer(s) registered")
    facts = collect(dataset)

    # Re-validate on the way out. Facts are validated at construction, but this
    # guarantees anything reaching disk survives a round trip through the model.
    payload = [fact.model_dump(mode="json") for fact in facts]
    for raw in payload:
        Fact.model_validate(raw)

    rows = source_rows(dataset, facts)

    # Quotes exempt their numerals from the claim check, so each one is matched
    # against the real field values of the rows its fact cites. Internal
    # consistency is not enough: an unchecked quote is a way to launder an
    # invented number past the guardrail.
    quoted = 0
    for fact in facts:
        quoted += len(verify_quotes(fact, rows))

    # The whole-book ranking is not a fact - it is an ordering over clients,
    # most of whom we have deliberately not gone deep on. It rides in the
    # envelope so the console can render the Monday view without re-deriving it.
    config = triage.load_config()
    ranking = [
        {
            "rank": index + 1,
            "client_id": score.client_id,
            "client_name": score.client_name,
            "score": round(score.total, 2),
            "signals": {k: round(v, 2) for k, v in score.signals.items()},
        }
        for index, score in enumerate(triage.rank_book(dataset, config))
    ]

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": LATEST_SNAPSHOT,
        "clients": list(CLIENTS),
        "fact_count": len(payload),
        "facts": payload,
        "source_rows": rows,
        "triage": {"weights": config["weights"], "ranking": ranking},
    }

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "facts.json"
    out.write_text(json.dumps(envelope, indent=2) + "\n")
    print(
        f"wrote {out.name}: {len(payload)} facts, {len(rows)} source rows, "
        f"{len(ranking)} clients ranked, {quoted} quotes verified against source"
    )
    return envelope


if __name__ == "__main__":
    build()
