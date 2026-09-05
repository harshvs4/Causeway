"""Render build/facts.json into an Obsidian vault.

The vault is the traceability argument made visible: open a client, click a
fact, click a source, and you are looking at the row. Obsidian's graph view
shows the whole chain at once, which is the point.

Two structural rules:
  - Verified/ holds only settled facts. A scenario fact reaching it is a bug
    and raises rather than being written.
  - `|` is a wikilink separator, so it can never appear in a note name. Row
    refs are sanitised on the way in, consistently, by one function.
"""

from __future__ import annotations

import json
import shutil
from collections import OrderedDict, defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from engine.loader import BUILD_DIR, REPO_ROOT, load

VAULT_DIR = REPO_ROOT / "vault"
TEMPLATE_DIR = REPO_ROOT / "templates"


class ScenarioInVerified(RuntimeError):
    """A forward-looking fact was about to be filed as settled. Never allow it."""


def source_note_name(file: str, row_ref: str) -> str:
    """A wikilink-safe, unique note name for one source row."""
    stem = file.replace(".csv", "").replace(".json", "")
    safe_ref = row_ref.replace("|", "__").replace("/", "-")
    return f"{stem}__{safe_ref}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["source_note"] = source_note_name
    return env


def build_vault() -> Path:
    envelope = json.loads((BUILD_DIR / "facts.json").read_text())
    facts = envelope["facts"]
    source_rows = envelope["source_rows"]
    as_of = envelope["as_of"]
    dataset = load()
    env = _env()

    if VAULT_DIR.exists():
        shutil.rmtree(VAULT_DIR)          # regeneration is idempotent by construction
    for folder in ("Clients", "Verified", "Sources"):
        (VAULT_DIR / folder).mkdir(parents=True)

    # --- facts ------------------------------------------------------------
    fact_template = env.get_template("fact.md.j2")
    clients_index = dataset.clients.set_index("client_id")
    cited_by: dict[str, list[str]] = defaultdict(list)

    for fact in facts:
        if fact["confidence"] == "scenario" or fact["kind"] == "scenario":
            raise ScenarioInVerified(
                f"{fact['fact_id']} is forward-looking and may not be written to "
                f"Verified/. Scenario rendering arrives in Phase 3."
            )
        client_name = str(clients_index.loc[fact["client_id"], "client_name"])
        rendered = fact_template.render(
            fact=fact, client_note=client_name, as_of=as_of
        )
        (VAULT_DIR / "Verified" / f"{fact['fact_id']}.md").write_text(rendered)
        for source in fact["sources"]:
            key = f"{source['file']}::{source['row_ref']}"
            cited_by[key].append(fact["fact_id"])

    # --- source rows ------------------------------------------------------
    source_template = env.get_template("source.md.j2")
    cited_fields: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        for source in fact["sources"]:
            key = f"{source['file']}::{source['row_ref']}"
            cited_fields[key].update(source["fields"])

    for key, row in source_rows.items():
        file, row_ref = key.split("::", 1)
        folder = VAULT_DIR / "Sources" / file.replace(".csv", "").replace(".json", "")
        folder.mkdir(parents=True, exist_ok=True)
        rendered = source_template.render(
            file=file,
            row_ref=row_ref,
            row=row,
            cited_fields=cited_fields[key],
            cited_by=sorted(set(cited_by[key])),
        )
        (folder / f"{source_note_name(file, row_ref)}.md").write_text(rendered)

    # --- context rows -----------------------------------------------------
    # The client page cites clients.csv and portfolios.csv directly, so those
    # rows get source notes too. Without them the page would carry wikilinks
    # into empty space, which is worse than no link at all.
    from engine.sources import resolve as resolve_row

    for client_id in envelope["clients"]:
        context: list[tuple[str, str]] = [("clients.csv", client_id)]
        owned = dataset.portfolios[dataset.portfolios["client_id"] == client_id]
        context += [("portfolios.csv", str(p)) for p in owned["portfolio_id"]]
        for file, row_ref in context:
            key = f"{file}::{row_ref}"
            folder = VAULT_DIR / "Sources" / file.replace(".csv", "")
            folder.mkdir(parents=True, exist_ok=True)
            rendered = source_template.render(
                file=file,
                row_ref=row_ref,
                row=resolve_row(dataset, file, row_ref),
                cited_fields=cited_fields[key],
                cited_by=sorted(set(cited_by[key])),
            )
            (folder / f"{source_note_name(file, row_ref)}.md").write_text(rendered)

    # --- clients ----------------------------------------------------------
    client_template = env.get_template("client.md.j2")
    portfolios = dataset.portfolios
    for client_id in envelope["clients"]:
        client = clients_index.loc[client_id]
        own = [f for f in facts if f["client_id"] == client_id]
        grouped: "OrderedDict[str, list[dict]]" = OrderedDict()
        for fact in sorted(own, key=lambda f: (-f["severity"], f["fact_id"])):
            grouped.setdefault(fact["kind"], []).append(fact)

        rendered = client_template.render(
            client={"client_id": client_id, **{k: client[k] for k in client.index}},
            facts_by_kind=grouped,
            portfolios=[
                {k: row[k] for k in row.index}
                for _, row in portfolios[portfolios["client_id"] == client_id].iterrows()
            ],
            as_of=as_of,
        )
        (VAULT_DIR / "Clients" / f"{client['client_name']}.md").write_text(rendered)

    notes = sum(1 for _ in VAULT_DIR.rglob("*.md"))
    print(f"wrote vault/: {notes} notes "
          f"({len(facts)} facts, {len(source_rows)} sources, "
          f"{len(envelope['clients'])} client(s))")
    return VAULT_DIR


if __name__ == "__main__":
    build_vault()
