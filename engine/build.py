"""Run every analyzer and emit build/facts.json.

Phase 0 ships this with an empty analyzer registry: the envelope, the write
path and the re-validation loop all work, and each analyzer added from Phase 1
onward is a one-line registration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Iterable

from engine.loader import BUILD_DIR, LATEST_SNAPSHOT, Dataset, load
from engine.models import Fact

SCHEMA_VERSION = 1

# Populated from Phase 1. Each entry: (name, callable taking Dataset -> Facts).
ANALYZERS: list[tuple[str, Callable[[Dataset], Iterable[Fact]]]] = []


def collect(dataset: Dataset) -> list[Fact]:
    facts: list[Fact] = []
    for name, analyzer in ANALYZERS:
        produced = list(analyzer(dataset))
        print(f"  {name:<14} {len(produced):>4} facts")
        facts.extend(produced)
    return facts


def build() -> dict:
    dataset = load()
    print(f"loaded dataset; {len(ANALYZERS)} analyzer(s) registered")
    facts = collect(dataset)

    # Re-validate on the way out. Facts are validated at construction, but this
    # guarantees anything that reaches disk survives a round trip through the
    # model - the vault, console and MCP server all read this file and trust it.
    payload = [fact.model_dump(mode="json") for fact in facts]
    for raw in payload:
        Fact.model_validate(raw)

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": LATEST_SNAPSHOT,
        "fact_count": len(payload),
        "facts": payload,
    }

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "facts.json"
    out.write_text(json.dumps(envelope, indent=2) + "\n")
    print(f"wrote {out.relative_to(out.parents[1])} ({len(payload)} facts)")
    return envelope


if __name__ == "__main__":
    build()
