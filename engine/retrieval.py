"""Finding the fact a conversation has just reached.

Shared by the MCP tools and the live Assist stream so both rank the same way -
if a rehearsal and a real call disagreed about which fact is relevant, the
rehearsal would be training the RM for a system that does not exist.

BM25 over fact text, with common words stripped. On documents this short the
filler dominates otherwise: "why is my collateral under pressure" comes back
with whichever fact happens to contain the most of "is", "my" and "under".
Deterministic and instant, which matters more here than semantic nuance - a cue
that arrives late in a live call is worse than no cue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")

_GRAMMATICAL = frozenset(
    """a an and are as at be been but by can did do does for from had has have
    he her his how i if in into is it its me my no not of on or our out she so
    that the their them then there these they this to under up us was we were
    what when where which who why will with would you your about just really
    quite very some any also then now here get got let say said tell told
    think""".split()
)

# A live call is mostly not about the portfolio, and these are what the rest of
# it is made of.
#
# Measured before they were added: three of five small-talk lines surfaced cue
# cards. "how was the trip back" matched a collateral cure on the single token
# "back", out of "came back under its trigger". The lesson is that a term can
# be *rare* in the corpus - and therefore score highly - while carrying no
# intent whatsoever, so neither a score threshold nor an IDF gate catches this:
# the score distributions for small talk and real questions overlap, and
# "how did that get cured" scores identically to "how was the trip back".
#
# Applied to documents and queries alike, so nothing can match on one side only.
# Most of these do appear somewhere in the fact corpus and losing them costs
# little - a cure is still reachable by "cured", "trigger" or "LTV".
#
# "us" sits in _GRAMMATICAL above rather than here: it is the pronoun that was
# missing from an otherwise complete set beside we/our/me/my, and it was
# matching "US Technology Leaders Fund" on "let us find a time next month".
_CONVERSATIONAL = frozenset(
    """back time times month months week weeks day days year years
    morning afternoon evening tomorrow yesterday today tonight next last
    meeting call trip holiday family children kids wife husband weather
    find move moving reschedule schedule speak spoke talk talking catch
    hope hoping good great fine well thanks thank please sorry
    been being come came going went keep keeps make makes made take
    takes""".split()
)

STOPWORDS = _GRAMMATICAL | _CONVERSATIONAL


_SUFFIXES = (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", ""), ("e", ""))
_MIN_STEM = 3

# How many times a fact's own kind appears in its indexed text.
#
# Tuned against the labelled set in tests/eval_assist.py rather than by taste,
# and the sweep is worth recording because tuning against an eval is how evals
# get gamed:
#
#     weight   answered  precision  recall   silence
#        1       0.879     0.500     0.680    1.000
#        3       0.879     0.485     0.660    1.000
#        5       0.909     0.507     0.700    1.000
#        8       0.909     0.507     0.700    1.000
#
# Five is taken as the smallest value on the plateau. Two things make this a
# real improvement rather than a fitted one: every metric moves the same way at
# once - precision and silence included, so nothing was traded for recall - and
# 8 is identical to 5, which is the shape of a plateau rather than a spike.
#
# Repeating the token is crude but necessary: BM25Okapi takes a flat bag, so
# there is no field to weight. One occurrence was not enough because term
# frequency saturates at k1 and the extra tokens lengthen the document, which
# the length normalisation then penalises - the two effects partly cancel, and
# three was the worst of both.
KIND_WEIGHT = 5


def stem(token: str) -> str:
    """Fold a word to a comparable root.

    Not linguistics - just enough that "breach" and "breached" are the same
    query. Without it, asking "did he breach the facility" ranked the driver
    fact at 3.13 and the breach fact itself at 0.61, purely because one text
    says "breach" as a noun and the other says "breached" as a verb. Applied
    identically to queries and documents, so any over-stemming is symmetric
    and cannot create a match that the other side does not also make.
    """
    for suffix, replacement in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            return token[: -len(suffix)] + replacement
    return token


def tokenise(text: str) -> list[str]:
    """Words worth matching on.

    Single characters are dropped as well as stopwords: an apostrophe splits
    "he's" into "he" and "s", and that stray "s" then matches "client's" and
    "account(s)" wherever they appear, which is how a question about collateral
    came back with a concentration fact.
    """
    return [
        stem(token)
        for token in _TOKEN.findall(text.lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


SYNONYMS_PATH = Path(__file__).resolve().parent / "config" / "retrieval_synonyms.yaml"


@lru_cache(maxsize=1)
def load_synonyms(path: str | None = None) -> dict[str, tuple[str, ...]]:
    """Spoken term -> corpus terms, stemmed on both sides.

    Flattened from the two sections of the config, which are separated for the
    reader rather than for the code: `morphology` is about English and would
    hold for any book, `vocabulary` is about this dataset's naming and would
    not.

    Stemming the map at load time is what keeps it consistent with the query it
    expands - an entry written as "diversifying" has to match the token
    tokenise() actually produces, not the word as typed.
    """
    import yaml

    config = yaml.safe_load(Path(path or SYNONYMS_PATH).read_text()) or {}
    flattened: dict[str, tuple[str, ...]] = {}
    for section in ("morphology", "vocabulary"):
        for spoken, written in (config.get(section) or {}).items():
            key = stem(str(spoken).lower())
            expansions = tuple(stem(str(w).lower()) for w in written)
            # A term appearing in both sections merges rather than overwrites.
            flattened[key] = tuple(dict.fromkeys(flattened.get(key, ()) + expansions))
    return flattened


def expand(tokens: Sequence[str],
           synonyms: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """A query's tokens, plus the corpus words they stand in for.

    Query side only. The original tokens are kept and the expansions appended,
    so a question that already used the data's own vocabulary ranks exactly as
    it did before - expansion can add a way to find a fact, never take one
    away. Duplicates are kept out so one spoken word cannot outweigh the rest
    of the sentence simply by expanding into three.
    """
    synonyms = load_synonyms() if synonyms is None else synonyms
    out = list(tokens)
    for token in tokens:
        for expansion in synonyms.get(token, ()):
            if expansion not in out:
                out.append(expansion)
    return out


_IDENTIFIER = re.compile(r"^[A-Z]{1,5}-[A-Z0-9-]+$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def context_terms(rows: Iterable[dict]) -> str:
    """Vocabulary from the rows a fact cites.

    An RM says "Lombard", "margin call", "the accumulator". The engine's own
    prose says "CF-0001" and "trigger". Those never match, so a question about
    the Lombard line retrieved nothing at all. The words the client and the RM
    actually use live on the cited rows - facility_type is "Lombard Credit
    Facility", instrument_name is "Helios Cloud Systems Inc" - so the index
    reads them too. Identifiers and dates are skipped: they add no vocabulary
    and would let a stray number match.
    """
    terms: list[str] = []
    for row in rows:
        for value in (row or {}).values():
            if not isinstance(value, str) or len(value) < 4:
                continue
            if _IDENTIFIER.match(value) or _ISO_DATE.match(value):
                continue
            terms.append(value)
    return " ".join(terms)


@dataclass(frozen=True)
class Hit:
    fact: dict
    score: float

    @property
    def fact_id(self) -> str:
        return self.fact["fact_id"]


class FactIndex:
    """A per-client BM25 index over headline, detail and kind."""

    def __init__(self, facts: Sequence[dict], source_rows: dict | None = None):
        self.facts = list(facts)
        source_rows = source_rows or {}
        self._corpus = []
        for fact in self.facts:
            cited = [
                source_rows.get(f"{s['file']}::{s['row_ref']}", {})
                for s in fact.get("sources", [])
            ]
            # The kind is the fact's topic, so a question that names it should
            # prefer that cluster. Repeated rather than scored separately
            # because BM25Okapi takes a flat bag of tokens, and one occurrence
            # was not enough: "worried about the collateral position" ranked
            # three single-position facts above all four collateral ones, on
            # the strength of the word "position" alone. Measured by make eval.
            document = (
                f"{fact['headline']} {fact.get('detail', '')} "
                f"{(fact['kind'] + ' ') * KIND_WEIGHT}"
                f"{context_terms(cited)}"
            )
            self._corpus.append(tokenise(document))
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def search(
        self,
        query: str,
        limit: int = 3,
        min_score: float = 0.0,
        relative_cutoff: float = 0.0,
    ) -> list[Hit]:
        """Rank facts for a query.

        `relative_cutoff` keeps hits scoring at least that fraction of the best
        hit. BM25 scores are not comparable across corpora - excluding one kind
        of fact shifted every score in this index by enough to drop a whole
        cluster under a fixed threshold - so what matters is the gap between
        the leaders and the rest, not any absolute constant. On "worried about
        the collateral" the four facility facts sit within 0.03 of each other
        and the next fact is 40% lower; a relative cutoff sees that shape, an
        absolute one cannot.
        """
        if not self._bm25:
            return []
        tokens = tokenise(query)
        if not tokens:
            return []
        # The question is expanded, never the corpus. See engine/config/
        # retrieval_synonyms.yaml: "coal" has to be able to reach a holding
        # the data calls "Bara Nusantara Energy Tbk", and no ranking change
        # reaches a word that is simply absent.
        tokens = expand(tokens)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(self.facts, scores), key=lambda pair: -pair[1])
        if not ranked or ranked[0][1] <= min_score:
            return []
        floor = max(min_score, ranked[0][1] * relative_cutoff)
        return [
            Hit(fact, float(score))
            for fact, score in ranked[: max(1, limit)]
            if score >= floor and score > 0
        ]


def index_by_client(facts: Iterable[dict],
                    source_rows: dict | None = None) -> dict[str, FactIndex]:
    grouped: dict[str, list[dict]] = {}
    for fact in facts:
        grouped.setdefault(fact["client_id"], []).append(fact)
    return {
        client: FactIndex(items, source_rows)
        for client, items in grouped.items()
    }
