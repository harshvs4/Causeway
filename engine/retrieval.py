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
from typing import Iterable, Sequence

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """a an and are as at be been but by can did do does for from had has have
    he her his how i if in into is it its me my no not of on or our out she so
    that the their them then there these they this to under up was we were what
    when where which who why will with would you your about just really quite
    very some any also then now here get got let say said tell told think""".split()
)


_SUFFIXES = (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", ""), ("e", ""))
_MIN_STEM = 3


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
            document = (
                f"{fact['headline']} {fact.get('detail', '')} {fact['kind']} "
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
