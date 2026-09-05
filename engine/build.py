"""Run every analyzer and emit build/facts.json.

The envelope carries the facts *and* the source rows they point at, so the
vault, the console and the MCP server all read one file and none of them needs
to touch data/ or re-derive anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Iterable

from engine.analyzers import collateral, lookthrough
from engine.loader import BUILD_DIR, LATEST_SNAPSHOT, Dataset, load
from engine.models import Fact
from engine.sources import resolve, source_key

SCHEMA_VERSION = 1

# Clients we have gone deep on. Depth over breadth is the plan.
CLIENTS: tuple[str, ...] = ("CL-0002",)

ANALYZERS: list[tuple[str, Callable[[Dataset], Iterable[Fact]]]] = [
    ("lookthrough", lambda ds: lookthrough.run(ds, CLIENTS)),
    ("collateral", lambda ds: collateral.run(ds, CLIENTS)),
]


def collect(dataset: Dataset) -> list[Fact]:
    facts: list[Fact] = []
    for name, analyzer in ANALYZERS:
        produced = list(analyzer(dataset))
        print(f"  {name:<14} {len(produced):>4} facts")
        facts.extend(produced)
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

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": LATEST_SNAPSHOT,
        "clients": list(CLIENTS),
        "fact_count": len(payload),
        "facts": payload,
        "source_rows": rows,
    }

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "facts.json"
    out.write_text(json.dumps(envelope, indent=2) + "\n")
    print(f"wrote {out.name}: {len(payload)} facts, {len(rows)} source rows")
    return envelope


if __name__ == "__main__":
    build()
