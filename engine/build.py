"""Run every analyzer and emit build/facts.json.

The envelope carries the facts *and* the source rows they point at, so the
vault, the console and the MCP server all read one file and none of them needs
to touch data/ or re-derive anything.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Callable, Iterable

from engine.analyzers import (
    attribution,
    collateral,
    liquidity,
    live_mark,
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

# Clients carrying hand-authored artefacts beyond what the engine derives: a
# rehearsal persona, and golden-number tests recomputed from the raw rows by
# hand. "Depth over breadth" lives here, not in the engine's coverage - the
# analyzers are deterministic, so running them over the whole book costs a
# groupby and hides nothing, whereas a persona is written one client at a time.
DEEP_CLIENTS: tuple[str, ...] = ("CL-0002", "CL-0001")

# Narrow the build to specific clients, comma-separated. Exists so a demo can
# cut the book down without editing code; unset means the whole book.
CLIENTS_ENV = "ANCHORLINE_CLIENTS"

ANALYZERS: list[tuple[str, Callable[[Dataset, tuple[str, ...]], Iterable[Fact]]]] = [
    ("attribution", attribution.run),
    ("lookthrough", lookthrough.run),
    ("collateral", collateral.run),
    ("tension", tension.run),
    ("liquidity", liquidity.run),
    ("mandate", mandate.run),
    ("triage", triage.run),
    ("scenario", scenario.run),
]


class DuplicateFactId(RuntimeError):
    """Two facts share an id. They would silently overwrite each other."""


class UnknownClient(ValueError):
    """A requested client is not in the dataset. Silently analysing nothing
    would look identical to a client with no findings."""


def clients_in(dataset: Dataset) -> tuple[str, ...]:
    """Every client in the dataset, in file order."""
    return tuple(str(client_id) for client_id in dataset.clients["client_id"])


def selected_clients(dataset: Dataset) -> tuple[str, ...]:
    """Which clients this build covers. The whole book unless told otherwise."""
    raw = os.environ.get(CLIENTS_ENV, "").strip()
    if not raw:
        return clients_in(dataset)
    wanted = tuple(part.strip() for part in raw.split(",") if part.strip())
    known = set(clients_in(dataset))
    unknown = [client_id for client_id in wanted if client_id not in known]
    if unknown:
        raise UnknownClient(
            f"{CLIENTS_ENV} names client(s) not in the dataset: {unknown}"
        )
    return wanted


def collect(dataset: Dataset, client_ids: tuple[str, ...] | None = None) -> list[Fact]:
    client_ids = clients_in(dataset) if client_ids is None else client_ids
    facts: list[Fact] = []
    for name, analyzer in ANALYZERS:
        produced = list(analyzer(dataset, client_ids))
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


def source_rows(
    dataset: Dataset,
    facts: list[Fact],
    extra: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Every row any fact cites, resolved once.

    `extra` holds origins that are not rows in data/ - a live quote, a
    sensitivity rule - already rendered as rows. They resolve through the same
    call, so nothing downstream needs to know which kind it is looking at.
    """
    rows: dict[str, dict] = {}
    for fact in facts:
        for source in fact.sources:
            key = source_key(source.file, source.row_ref)
            if key not in rows:
                rows[key] = resolve(dataset, source.file, source.row_ref, extra)
    return rows


def live_facts(
    dataset: Dataset, clients: tuple[str, ...]
) -> tuple[list[Fact], dict[str, dict]]:
    """Facts marked against today's market, and the quote rows behind them.

    Returns nothing at all unless a live price source was explicitly asked for.
    That default is load-bearing: `make build` has to stay reproducible, and the
    goldens in tests/ are pinned to four decimals. A build that quietly reached
    for the network would make 24.6400% a function of this morning's tape.
    """
    from engine.live_feed import SOURCE_ENV, fetch_all, quote_source
    from engine.marks import derive_marks

    if (os.environ.get(SOURCE_ENV) or "snapshot").strip().lower() == "snapshot":
        return [], {}

    source = quote_source()
    quotes = fetch_all(source)
    marks = derive_marks(dataset, quotes)
    live = sum(1 for quote in quotes.values() if quote.live)
    moved = sum(1 for mark in marks.values() if mark.moved)
    print(
        f"  {'live_mark':<14} quotes: {live}/{len(quotes)} live, "
        f"{moved}/{len(marks)} instruments re-marked"
    )
    produced = list(live_mark.run(dataset, clients, quotes, marks))
    print(f"  {'live_mark':<14} {len(produced):>4} facts")
    return produced, live_mark.extra_source_rows(quotes, marks)


def build() -> dict:
    dataset = load()
    clients = selected_clients(dataset)
    print(
        f"loaded dataset; {len(ANALYZERS)} analyzer(s) registered, "
        f"{len(clients)} client(s) selected"
    )
    facts = collect(dataset, clients)

    # Marked against today's market, when asked for. Appended rather than merged
    # into collect(), so the snapshot path above stays exactly what it was.
    marked, extra = live_facts(dataset, clients)
    facts.extend(marked)

    # Re-validate on the way out. Facts are validated at construction, but this
    # guarantees anything reaching disk survives a round trip through the model.
    payload = [fact.model_dump(mode="json") for fact in facts]
    for raw in payload:
        Fact.model_validate(raw)

    rows = source_rows(dataset, facts, extra)

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
        "clients": list(clients),
        # Marked separately rather than filtered: the console and the vault both
        # need to say which clients carry hand-verified numbers and a rehearsal
        # persona, without implying the other eighteen were not analysed.
        "deep_clients": [c for c in DEEP_CLIENTS if c in clients],
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
