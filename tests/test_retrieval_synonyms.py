"""The synonym map may add ways to find a fact. It may not change the ranking.

Query expansion is the cheapest way to make retrieval worse: add enough terms
and every question matches everything. So the properties pinned here are not
"does coal find Bara" - `make eval` measures that - but the structural ones that
stop this file becoming a way to hand-tune the eval:

  - expansion touches the query only, never the corpus
  - a question already using the data's own words ranks exactly as before
  - every target actually appears in some fact, or the entry is dead weight
  - one spoken word cannot outweigh a sentence by expanding into three
"""

import pytest

import assist.app as assist_app
from engine.retrieval import (
    SYNONYMS_PATH,
    FactIndex,
    expand,
    load_synonyms,
    stem,
    tokenise,
)


@pytest.fixture(scope="module")
def synonyms() -> dict[str, tuple[str, ...]]:
    return load_synonyms()


@pytest.fixture(scope="module")
def corpus_vocabulary() -> set[str]:
    """Every stem the live indexes actually contain, across all clients."""
    assist_app.load_facts()
    vocabulary: set[str] = set()
    for index in assist_app._indexes.values():
        for document in index._corpus:
            vocabulary |= set(document)
    return vocabulary


# --- the map has to be honest ----------------------------------------------

def test_every_target_appears_in_some_fact(synonyms, corpus_vocabulary):
    """A synonym pointing at a word that is in no fact matches nothing, and
    misleads the next reader into thinking it does something. "concentration"
    was exactly that - it appears in fact_ids, which are not indexed."""
    dead = {
        spoken: [t for t in targets if t not in corpus_vocabulary]
        for spoken, targets in synonyms.items()
    }
    dead = {k: v for k, v in dead.items() if v}
    assert not dead, f"targets in no fact: {dead}"


def test_the_map_is_stemmed_on_both_sides(synonyms):
    """Written as words, matched as stems. An entry typed "diversifying" has to
    match the token tokenise() produces, or it silently never fires."""
    for spoken, targets in synonyms.items():
        assert stem(spoken) == spoken, spoken
        for target in targets:
            assert stem(target) == target, target


def test_no_stopword_is_expanded(synonyms):
    """Expanding a word tokenise() has already removed is dead code, and
    expanding *into* one adds a term the index cannot match."""
    from engine.retrieval import STOPWORDS

    for spoken, targets in synonyms.items():
        assert spoken not in STOPWORDS, spoken
        for target in targets:
            assert target not in STOPWORDS, f"{spoken} -> {target}"


def test_no_synonym_maps_a_word_to_itself(synonyms):
    for spoken, targets in synonyms.items():
        assert spoken not in targets, spoken


# --- expansion may only add -------------------------------------------------

def test_expansion_keeps_every_original_token(synonyms):
    tokens = tokenise("what did the coal position do")
    expanded = expand(tokens, synonyms)
    assert expanded[: len(tokens)] == tokens


def test_expansion_never_adds_a_token_twice(synonyms):
    """One spoken word must not outweigh the rest of a sentence by expanding
    into three copies of the same thing. "coal", "mining" and "mine" all point
    at bara/nusantara, and those must arrive once.

    Repeats among the *original* tokens are left alone: "mining and the mine"
    genuinely says it twice, and BM25 is entitled to weight that.
    """
    tokens = tokenise("coal mining and the mine")
    added = expand(tokens, synonyms)[len(tokens):]
    assert len(added) == len(set(added))
    assert set(added) == {"bara", "nusantara"}


def test_a_question_in_the_datas_own_words_is_unchanged(synonyms):
    """The property that makes this safe: expansion can add a route to a fact,
    never remove or re-order one. A question already using the corpus
    vocabulary has to score exactly as it did before the map existed."""
    tokens = tokenise("why did the facility breach its margin call trigger")
    assert expand(tokens, synonyms) == tokens


def test_expansion_is_query_side_only():
    """Documents are never rewritten. If they were, every BM25 score would move
    at once and the goldens pinned elsewhere would shift silently.

    Three documents, not one or two. BM25Okapi's inverse document frequency is
    log(N - df + 0.5) - log(df + 0.5): with one document every term scores
    negative, and with two a term in exactly one of them scores *exactly zero*.
    Either would make this pass or fail for a reason that has nothing to do
    with expansion.
    """
    def fact(fact_id: str, headline: str) -> dict:
        return {
            "fact_id": fact_id, "client_id": "CL-TEST", "kind": "collateral",
            "headline": headline, "detail": "", "sources": [],
        }

    index = FactIndex([
        fact("F-TEST-1", "Bara Nusantara Energy Tbk fell sharply."),
        fact("F-TEST-2", "Helios Cloud Systems Inc rose."),
        fact("F-TEST-3", "Meridian Semiconductor Corp was unchanged."),
    ])
    # "coal" reaches the Bara document by expansion, while the document itself
    # never gains the word "coal".
    assert "coal" not in index._corpus[0]
    hits = index.search("coal", limit=1)
    assert hits and hits[0].fact_id == "F-TEST-1"


# --- the two cases the map exists for --------------------------------------

def test_diversify_reaches_the_diversification_finding():
    """The stemmer strips inflection, not derivation: the client says the verb
    and the fact says the noun, and no suffix rule bridges them."""
    assist_app.load_facts()
    hits = assist_app._indexes["CL-0002"].search(
        "he keeps saying he wants to diversify",
        limit=3, min_score=0.15, relative_cutoff=0.6,
    )
    assert any(h.fact_id == "F-CL0002-TENSION-DIVERSIFICATION" for h in hits)


def test_coal_reaches_a_holding_the_data_calls_bara_nusantara():
    """clients.csv records "family coal mining and energy group"; the holding is
    "Bara Nusantara Energy Tbk" and contains no "coal" at all."""
    assist_app.load_facts()
    hits = assist_app._indexes["CL-0001"].search(
        "what did the coal position do",
        limit=5, min_score=0.15, relative_cutoff=0.6,
    )
    assert hits
    assert "Bara Nusantara" in " ".join(h.fact["headline"] for h in hits)


def test_an_unknown_word_expands_to_nothing(synonyms):
    tokens = tokenise("zebra parachute quantum")
    assert expand(tokens, synonyms) == tokens


def test_either_section_alone_is_a_valid_map(tmp_path):
    """Both sections optional, so a deployment can ship the language half
    without the dataset-specific half."""
    path = tmp_path / "retrieval_synonyms.yaml"
    path.write_text("morphology:\n  diversify: [diversification]\n")
    assert load_synonyms(str(path)) == {"diversify": ("diversification",)}


def test_the_shipped_map_is_where_the_code_looks_for_it():
    assert SYNONYMS_PATH.exists()
    assert SYNONYMS_PATH.name == "retrieval_synonyms.yaml"
