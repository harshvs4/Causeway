"""A rehearsal persona for any client, built only from what is on record.

    make personas                                          # every client
    .venv/bin/python -m mcp_server.personas.generate CL-0014

One hand-written persona does not scale, and "what happens for a client you did
not prepare?" is the first question anyone asks about Rehearse. This answers it
for all twenty.

**Nothing here is paraphrased.** The client's positions arrive as verbatim note
text with the note_id printed beside them, because a paraphrase of what a client
said is no longer what they said - and a rehearsal against a paraphrase trains
the RM for a conversation that never happened. The generator's own prose is
confined to behavioural scaffolding, which is either constant or selected by a
data check, and is never written about a particular person.

The hand-written mcp_server/personas/CL-0002_rehearse.md stays where it is and
is never overwritten. It carries judgement a generator cannot have - it reads
Ravi's notes and says what kind of man makes those decisions - and treating the
generated article as its equal would be the same overclaim this codebase avoids
everywhere else. Generated personas land in generated/ beside it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from engine.loader import BUILD_DIR, REPO_ROOT, load
from mcp_server.server import RESISTANCE_MARKERS

TEMPLATE_DIR = REPO_ROOT / "templates"
OUTPUT_DIR = Path(__file__).resolve().parent / "generated"
TEMPLATE_NAME = "persona.md.j2"

# A facility this close to its trigger cannot absorb more borrowing, whatever
# the client assumes. Matches the steep end of the triage collateral curve.
TIGHT_HEADROOM_PP = 5.0


def _recorded(value):
    """None when the field is genuinely absent, so the template can say so.

    CL-0017 is a family office and has no age: the column is NaN. Rendering
    that through an int filter produced "Age | 0", which is a fabricated fact
    about a client - the precise failure the rest of this codebase is built to
    prevent, arriving through a Jinja filter. A missing field has to stay
    missing all the way to the page.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:      # NaN
        return None
    if str(value).strip() in ("", "nan", "NaT"):
        return None
    return value


def notes_for(dataset, client_id: str) -> list[dict]:
    """This client's notes, newest first, verbatim.

    resistance_markers is the same keyword check the MCP tool exposes, imported
    rather than re-listed so the two cannot drift apart. It can only under-fire:
    a note with no marker may still record a refusal, which is why the full text
    is always rendered and the template says so.
    """
    notes = dataset.rm_notes
    notes = notes[notes["client_id"] == client_id].sort_values(
        "note_date", ascending=False
    )
    out: list[dict] = []
    for _, note in notes.iterrows():
        text = str(note["note"])
        lowered = text.lower()
        out.append(
            {
                "note_id": str(note["note_id"]),
                "date": note["note_date"].strftime("%Y-%m-%d"),
                "channel": str(note["channel"]),
                "note": text,
                "resistance_markers": [m for m in RESISTANCE_MARKERS if m in lowered],
            }
        )
    return out


def pressure_points(envelope: dict | None, client_id: str) -> list[str]:
    """Where this client is genuinely vulnerable, read off the findings.

    Derived, not imagined. The two that make a rehearsal bite are the two the
    engine can actually evidence: a facility with no room left to borrow
    against, and a commitment the book cannot fund. Each names its fact_id so
    the agent looks the figure up rather than being handed it - a persona must
    never carry a number the agent could then state without a tool call.
    """
    if not envelope:
        return []

    points: list[str] = []
    facts = [f for f in envelope.get("facts", []) if f.get("client_id") == client_id]

    for fact in facts:
        numbers = fact.get("numbers") or {}
        distance = numbers.get("distance_pp")
        if (
            fact.get("kind") == "collateral"
            and distance is not None
            and distance <= TIGHT_HEADROOM_PP
        ):
            points.append(
                f"that borrowing further against the portfolio is not as "
                f"available to you as you assume — see `{fact['fact_id']}`"
            )
            break

    for fact in facts:
        numbers = fact.get("numbers") or {}
        if fact.get("kind") == "liquidity" and numbers.get("shortfall", 0) > 0:
            points.append(
                f"that something you have already committed to cannot be funded "
                f"from what is actually sellable — see `{fact['fact_id']}`"
            )
            break

    return points


def _envelope() -> dict | None:
    path = BUILD_DIR / "facts.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def persona_for(dataset, client_id: str, envelope: dict | None = None,
                env: Environment | None = None) -> str:
    """Render one persona. Raises if the client is not in the dataset."""
    clients = dataset.clients.set_index("client_id")
    if client_id not in clients.index:
        raise KeyError(f"unknown client_id {client_id!r}")
    row = clients.loc[client_id]

    notes = notes_for(dataset, client_id)
    client = {"client_id": client_id, **{k: _recorded(row[k]) for k in row.index}}
    template = (env or _env()).get_template(TEMPLATE_NAME)

    return template.render(
        client=client,
        # The RM is one known person, referred to by given name in the notes
        # themselves. The *client* is deliberately never shortened: splitting a
        # name on whitespace and taking the first part assumes given-name-first
        # order, and across this book it does not hold - it would address Lau
        # Chi Ming by his family name. Full name throughout costs nothing.
        rm_first_name=str(row["rm_name"]).split(" ")[0],
        notes=notes,
        any_resistance=any(n["resistance_markers"] for n in notes),
        pressure_points=pressure_points(envelope, client_id),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


def generate(
    client_ids: tuple[str, ...] = (), output_dir: Path | None = None
) -> list[Path]:
    """Write a persona per client. Returns the paths written."""
    dataset = load()
    envelope = _envelope()
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _env()

    wanted = client_ids or tuple(str(c) for c in dataset.clients["client_id"])
    written: list[Path] = []
    for client_id in wanted:
        rendered = persona_for(dataset, client_id, envelope, env)
        path = output_dir / f"{client_id}_rehearse.md"
        path.write_text(rendered)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "client_ids", nargs="*",
        help="client ids to generate; omit for the whole book",
    )
    args = parser.parse_args(argv)

    written = generate(tuple(args.client_ids))
    dataset = load()
    for path in written:
        client_id = path.stem.replace("_rehearse", "")
        notes = notes_for(dataset, client_id)
        flagged = sum(1 for note in notes if note["resistance_markers"])
        print(
            f"  {client_id}  {len(notes)} note(s), {flagged} with a resistance "
            f"marker  -> {path.relative_to(REPO_ROOT)}"
        )
    print(f"wrote {len(written)} persona(s) to {OUTPUT_DIR.relative_to(REPO_ROOT)}/")
    print(
        "the hand-written CL-0002_rehearse.md is untouched; it carries judgement "
        "a generator cannot"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
