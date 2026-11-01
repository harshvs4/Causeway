"""Put the obligation a finding bears on next to the finding, in MAS's words.

A mandate breach measured against the bank's own band is a true statement about
the bank's own rule. It is not, on its own, the thing a compliance reviewer is
looking at. This module attaches the external obligation - quoted verbatim, with
its paragraph number and the URL it came from - as an additional Source on the
facts where a person has written down that the connection holds.

Three constraints shape it, and all three are about not overclaiming.

**It never says "breach".** Every clause in the corpus is a suitability
obligation owed at the point a recommendation is made. Anchorline reads a book
that already exists and cannot see what was discussed, documented or disclosed
at the time, so it cannot know whether an obligation was met. Facts say a
finding *bears on* an obligation and then quote it. Deciding compliance is a
compliance officer's job.

**The mapping is hand-written.** engine/config/regulatory_map.yaml, with a
reason beside every entry and an explicit list of the finding types left
unmapped. A model inferring which paragraph applies to which finding would make
the citation unauditable, which in this domain above all it cannot be.

**The text is quoted, never summarised.** The corpus carries transcribed
excerpts and the Fact machinery treats them as quotations, so
engine.sources.verify_quotes checks each one against the clause it cites - the
same check that stops an invented portfolio number, pointed at the possibility
of an invented regulatory sentence.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from engine.loader import DATA_DIR
from engine.models import Source

CORPUS_PATH = DATA_DIR / "regulatory" / "mas_faa_n16.yaml"
MAP_PATH = Path(__file__).resolve().parent / "config" / "regulatory_map.yaml"

# How a Source names this origin. It is not a path under data/ that resolve()
# can key by row, so it is registered explicitly: a clause is cited by its id.
REGULATORY_FILE = "mas_faa_n16.yaml"


class RegulatoryConfigError(RuntimeError):
    """The corpus or the map is malformed. Refuse rather than mis-cite."""


@lru_cache(maxsize=1)
def load_corpus(path: str | None = None) -> dict:
    corpus = yaml.safe_load(Path(path or CORPUS_PATH).read_text())
    required = ("notice", "issued_under", "issue_date", "source_url",
                "retrieved", "clauses")
    missing = [key for key in required if key not in corpus]
    if missing:
        raise RegulatoryConfigError(
            f"{CORPUS_PATH.name} is missing {missing}. A clause without its "
            f"provenance cannot be checked, and being checkable is the only "
            f"thing that makes quoting a regulator legitimate."
        )
    for clause_id, clause in corpus["clauses"].items():
        for field in ("paragraph", "text"):
            if not str(clause.get(field, "")).strip():
                raise RegulatoryConfigError(f"{clause_id} has no {field}")
    return corpus


@lru_cache(maxsize=1)
def load_map(path: str | None = None) -> dict:
    mapping = yaml.safe_load(Path(path or MAP_PATH).read_text())
    if "findings" not in mapping:
        raise RegulatoryConfigError(f"{MAP_PATH.name} has no 'findings' section")

    known = set(load_corpus()["clauses"])
    for finding, entry in (mapping["findings"] or {}).items():
        clauses = entry.get("clauses") or []
        if not clauses:
            raise RegulatoryConfigError(
                f"{finding} maps to no clause; remove it, or list it under "
                f"'unmapped' with a reason"
            )
        unknown = [c for c in clauses if c not in known]
        if unknown:
            raise RegulatoryConfigError(
                f"{finding} cites clauses that are not in the corpus: {unknown}"
            )
        if not str(entry.get("why", "")).strip():
            raise RegulatoryConfigError(
                f"{finding} has no 'why'. A mapping from a portfolio finding to "
                f"a regulatory paragraph is a judgement, and a judgement whose "
                f"reasoning is not written down cannot be argued with."
            )
    return mapping


def clauses_for(finding: str) -> list[dict]:
    """The clauses a finding type bears on, each with its text and number.

    Returns [] for an unmapped finding type, which is a decision rather than a
    gap - the map's 'unmapped' section says why each one is empty.
    """
    entry = (load_map()["findings"] or {}).get(finding)
    if not entry:
        return []
    corpus = load_corpus()
    return [
        {"clause_id": clause_id, **corpus["clauses"][clause_id]}
        for clause_id in entry["clauses"]
    ]


def sources_for(finding: str) -> tuple[Source, ...]:
    """Sources citing each clause, for attaching to a Fact."""
    return tuple(
        Source(
            file=REGULATORY_FILE,
            row_ref=clause["clause_id"],
            fields=("paragraph", "text", "notice", "source_url"),
        )
        for clause in clauses_for(finding)
    )


def lead_clause(finding: str) -> dict | None:
    """The clause listed first; the map orders them by closeness of fit."""
    clauses = clauses_for(finding)
    return clauses[0] if clauses else None


def citation(finding: str) -> str:
    """How a fact refers to the obligation in its own text.

    Deliberately not a sentence about compliance. It names the notice and the
    paragraph and leaves the quoting to the fact, so rendered text reads "bears
    on MAS Notice FAA-N16 paragraph 11(h)" and never "breaches".
    """
    clause = lead_clause(finding)
    if clause is None:
        return ""
    return f"MAS Notice {load_corpus()['notice']} paragraph {clause['paragraph']}"


def annotation(finding: str) -> tuple[str, tuple[str, ...], tuple[Source, ...]]:
    """(detail fragment, quotes, sources) for one finding type.

    Returns empty everything for an unmapped finding, so a call site is one line
    and adding nothing is the default rather than an exception.

    Both the paragraph reference and the clause text are declared as quotes, and
    neither is decoration. "paragraph 11(h)" carries the numeral 11, and the
    Fact contract will refuse any numeral in rendered text that is not also a
    computed value - correctly, since 11 is not something this engine computed.
    Declaring it a quote exempts it, and engine.sources.verify_quotes then
    checks it against the `paragraph` field of the clause actually cited. The
    same applies to the obligation's own text, which in several paragraphs
    contains a section number. So the guardrail that stops an invented
    portfolio figure is pointed, unchanged, at the possibility of an invented
    regulatory sentence.
    """
    clause = lead_clause(finding)
    if clause is None:
        return "", (), ()

    corpus = load_corpus()
    paragraph = str(clause["paragraph"])
    text = str(clause["text"]).strip()
    fragment = (
        f" Bears on {corpus['notice']} paragraph {paragraph}, which requires: "
        f"\"{text}\" That is an obligation owed when a recommendation is made; "
        f"whether it was met is not something this finding can establish."
    )
    return fragment, (paragraph, text), sources_for(finding)


def clause_row(clause_id: str) -> dict:
    """One clause rendered as a source row, so it resolves like a dataset row.

    Carries its provenance rather than only its text: a reviewer opening this in
    the vault or the console gets the notice, the paragraph, the issue date, the
    URL and the date it was retrieved, which together are what make the
    quotation checkable against the regulator's own published document.
    """
    corpus = load_corpus()
    clause = corpus["clauses"].get(clause_id)
    if clause is None:
        raise RegulatoryConfigError(f"unknown clause {clause_id!r}")
    return {
        "clause_id": clause_id,
        "notice": corpus["notice"],
        "title": corpus["title"],
        "act": corpus["act"],
        "issued_under": corpus["issued_under"],
        "paragraph": clause["paragraph"],
        "heading": clause.get("heading", ""),
        "text": clause["text"],
        "preamble": clause.get("preamble", ""),
        "issue_date": corpus["issue_date"],
        "last_updated": corpus.get("last_updated", ""),
        "source_url": corpus["source_url"],
        "retrieved": corpus["retrieved"],
        "caveat": (
            "Quoted verbatim from a public MAS notice. This is a suitability "
            "obligation owed when a recommendation is made; Anchorline reads an "
            "existing book and does not determine whether it was met."
        ),
    }


def all_clause_rows() -> dict[str, dict]:
    """Every clause, keyed as engine.sources.source_key would key it."""
    return {
        f"{REGULATORY_FILE}::{clause_id}": clause_row(clause_id)
        for clause_id in load_corpus()["clauses"]
    }
