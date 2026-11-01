"""Quoting a regulator is where overclaiming is least forgivable.

Two things are tested here, and the second matters more than the first.

The first is mechanical: a clause cited by a fact resolves, the quote verifies
against the clause it cites, and the map cannot point at a paragraph the corpus
does not contain.

The second is what the product is allowed to say. Every clause in the corpus is
a suitability obligation owed at the point a recommendation is made. Anchorline
reads a book that already exists and cannot see what was discussed, documented
or disclosed at the time, so it cannot know whether an obligation was met. The
tests below refuse any fact that says otherwise - no "breach of", no "violates",
no "non-compliant". A tool telling an RM they are in breach of a MAS notice is
making a legal determination it has no standing to make, and that is a worse
failure than any wrong number.
"""

import json
import re

import pytest

from engine import regulatory
from engine.loader import BUILD_DIR, load
from engine.models import Fact
from engine.sources import resolve, source_key, verify_quotes

# Language that would assert a compliance conclusion rather than name an
# obligation. The product may say a finding "bears on" a paragraph. It may not
# say the paragraph was broken.
FORBIDDEN = (
    "breach of faa", "breaches faa", "in breach of mas", "violat",
    "non-compliant", "noncompliant", "contravene", "infringe",
    "regulatory breach", "compliance failure", "is unlawful", "illegal",
)


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def corpus():
    return regulatory.load_corpus()


@pytest.fixture(scope="module")
def mapping():
    return regulatory.load_map()


@pytest.fixture(scope="module")
def envelope():
    return json.loads((BUILD_DIR / "facts.json").read_text())


# --- the corpus carries its own provenance ---------------------------------

def test_every_clause_has_a_paragraph_and_verbatim_text(corpus):
    for clause_id, clause in corpus["clauses"].items():
        assert str(clause["paragraph"]).strip(), clause_id
        assert str(clause["text"]).strip(), clause_id


def test_the_corpus_says_where_it_came_from_and_when(corpus):
    """A regulatory citation a reviewer cannot check against the regulator's own
    document is worth less than no citation."""
    assert corpus["source_url"].startswith("https://www.mas.gov.sg/")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", corpus["issue_date"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", corpus["retrieved"])
    assert corpus["notice"] == "FAA-N16"
    # Transcribed from the document rather than recalled: a search summary
    # consulted while building this said section 58, and the Notice says 67.
    assert "section 67" in corpus["issued_under"]


# --- the map is a judgement, written down ----------------------------------

def test_every_mapped_finding_cites_clauses_that_exist(mapping, corpus):
    known = set(corpus["clauses"])
    for finding, entry in mapping["findings"].items():
        assert entry["clauses"], finding
        assert set(entry["clauses"]) <= known, finding


def test_every_mapped_finding_says_why(mapping):
    """A mapping from a portfolio finding to a regulatory paragraph is a
    judgement. One whose reasoning is not written down cannot be argued with."""
    for finding, entry in mapping["findings"].items():
        assert len(str(entry.get("why", "")).strip()) > 40, finding


def test_every_unmapped_kind_says_why_it_is_empty(mapping):
    """Silence has to be a decision. An analyzer kind with no clause and no
    reason reads like an oversight, and the next person adds one."""
    for kind, reason in mapping["unmapped"].items():
        assert len(str(reason).strip()) > 40, kind


def test_the_forward_looking_kind_is_never_mapped(mapping):
    """Attaching a regulatory obligation to a hypothesis would be the clearest
    possible way to blur the Verified/Scenario line."""
    assert "scenario" in mapping["unmapped"]
    assert not any(f.startswith("scenario") for f in mapping["findings"])


def test_an_unmapped_finding_gets_no_clause_and_no_citation():
    assert regulatory.clauses_for("attribution") == []
    assert regulatory.sources_for("attribution") == ()
    assert regulatory.citation("attribution") == ""
    assert regulatory.annotation("attribution") == ("", (), ())


def test_a_map_citing_an_unknown_clause_is_refused(tmp_path):
    import yaml

    broken = {"findings": {"bad": {"clauses": ["FAA-N16-999"], "why": "x" * 50}}}
    path = tmp_path / "regulatory_map.yaml"
    path.write_text(yaml.safe_dump(broken))
    regulatory.load_map.cache_clear()
    try:
        with pytest.raises(regulatory.RegulatoryConfigError,
                           match="not in the corpus"):
            regulatory.load_map(str(path))
    finally:
        regulatory.load_map.cache_clear()


# --- a cited clause resolves like any other source -------------------------

def test_a_clause_resolves_with_its_text_and_provenance(ds, corpus):
    row = resolve(ds, regulatory.REGULATORY_FILE, "FAA-N16-11h")
    assert row["paragraph"] == "11(h)"
    assert "substantial portion" in row["text"]
    assert row["source_url"] == corpus["source_url"]
    assert row["retrieved"] == corpus["retrieved"]
    assert "does not determine whether it was met" in row["caveat"]


def test_an_unknown_clause_is_an_error_not_an_invention(ds):
    with pytest.raises(regulatory.RegulatoryConfigError, match="unknown clause"):
        resolve(ds, regulatory.REGULATORY_FILE, "FAA-N16-NOPE")


# --- what the shipped facts actually say -----------------------------------

def _annotated(envelope) -> list[dict]:
    return [
        fact for fact in envelope["facts"]
        if any(s["file"] == regulatory.REGULATORY_FILE for s in fact["sources"])
    ]


def test_some_facts_actually_carry_a_clause(envelope):
    assert _annotated(envelope), "no fact cites a clause - the wiring is dead"


def test_no_fact_asserts_a_compliance_conclusion(envelope):
    """The line this product does not cross. It may name an obligation; it may
    not decide whether that obligation was met."""
    offenders = []
    for fact in envelope["facts"]:
        text = f"{fact['headline']} {fact['detail']}".lower()
        hits = [phrase for phrase in FORBIDDEN if phrase in text]
        if hits:
            offenders.append((fact["fact_id"], hits))
    assert not offenders, f"compliance conclusion asserted: {offenders}"


def test_an_annotated_fact_says_the_question_is_not_settled(envelope):
    for fact in _annotated(envelope):
        assert "Bears on" in fact["detail"], fact["fact_id"]
        assert "not something this finding can establish" in fact["detail"], (
            fact["fact_id"]
        )


def test_every_quoted_clause_verifies_against_the_clause_it_cites(ds, envelope):
    """The guardrail that stops an invented portfolio number, pointed at the
    possibility of an invented regulatory sentence. A quote has to appear in a
    row the fact actually cites."""
    rows: dict[str, dict] = {}
    for fact in _annotated(envelope):
        for source in fact["sources"]:
            key = source_key(source["file"], source["row_ref"])
            rows.setdefault(key, resolve(ds, source["file"], source["row_ref"]))

    checked = 0
    for raw in _annotated(envelope):
        resolved = verify_quotes(Fact.model_validate(raw), rows)
        checked += sum(
            1 for file, _ in resolved.values()
            if file == regulatory.REGULATORY_FILE
        )
    assert checked, "no clause text was verified - the check would be vacuous"


def test_the_clause_rows_reach_the_envelope(envelope):
    """So the console and the vault can show the paragraph beside the finding
    without going back to disk."""
    keys = [
        k for k in envelope["source_rows"]
        if k.startswith(regulatory.REGULATORY_FILE)
    ]
    assert keys
    for key in keys:
        row = envelope["source_rows"][key]
        assert row["text"] and row["paragraph"] and row["source_url"]


def test_clause_text_is_not_searchable(envelope):
    """Regulatory prose is cited for provenance, not vocabulary. Indexing it
    measurably hurt: with the clause rows in the index, "shall we move the
    Thursday meeting to the afternoon" matched a diversification finding and
    silence fell from 1.000 to 0.800."""
    from engine.retrieval import NON_VOCABULARY_SOURCES, FactIndex

    assert regulatory.REGULATORY_FILE in NON_VOCABULARY_SOURCES
    index = FactIndex(_annotated(envelope), envelope["source_rows"])
    vocabulary = {token for document in index._corpus for token in document}
    # Words that occur only in the clause provenance, never in a fact's own
    # prose. If these are in the index, the rows are being read again.
    assert "monetary" not in vocabulary
    assert "verbatim" not in vocabulary
