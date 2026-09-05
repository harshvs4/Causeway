# Anchorline

**AI wealth intelligence for relationship managers.**
SingHacks 2026 — Julius Baer track.

A private-bank RM can already see what a portfolio *is*. Anchorline explains what
*happened to it and why*, surfaces risk that no single report shows, and prepares
the RM for the conversation — before it, and during it.

Every claim traces to a row in the dataset. That is enforced by the code, not
promised in a caveat.

---

## The one rule

**The AI never speaks to a client.** It prepares the RM and supports the RM, in
private. The client only ever hears their relationship manager.

This is structural, not a policy note:

- The Assist service has **no audio output path at all**. There is no speaker to
  disable. A test asserts no route is named for speech.
- The Rehearse agent plays *the client*, against the RM, in private, using that
  client's real documented objections.
- Forward-looking analysis lives in a **different folder** from the record, and
  the vault builder raises rather than writing one into the other.

## The second rule

**No number is written where a number could be computed.**

Every fact is a typed object, and two invariants are enforced when it is
constructed — so a fact that violates either cannot exist in memory, let alone
reach a vault, a screen, or a voice agent:

1. Every fact carries at least one source.
2. Every number in the rendered text also appears in `numbers` — it was computed,
   not authored.

Verbatim quotations of source text are exempt from (2), and each quotation is
separately checked against the actual field value of a row the fact cites. An
unchecked quote would be a way to launder an invented number past the guardrail.

---

## What it found

Two clients, analysed in depth. These findings are reproducible from `data/` and
pinned as tests:

**Ravi Chandrasekaran (CL-0002)** holds Helios Cloud Systems directly at 14.00%
of his advisory portfolio *and* an equity-linked note on Helios at a further
10.64% — **24.64% in one name**, half of it filed under "Structured Products"
where no per-asset-class report will show it. That portfolio is pledged as
collateral. The facility breached its margin-call trigger at **75.64%** against
75.00%, and the counterfactual says this was **client action, not the market**:
holding collateral fixed, the drawdown moved LTV +21.84 points, while the market
was moving it −5.82. He drew USD 1.7m days after his RM warned him — matching her
note to the dollar, across two files.

Of the USD 13,207,200 that looks daily-tradeable on his statement, **USD 240,726**
can actually be sold before that facility breaches.

**Hartono Wijaya Kusuma (CL-0001)** states an objective of diversifying away from
the family business. His custody account is **97.97%** that business. Then the
look-through lands: a fixed coupon note he subscribed to sits in the *other*
account — the one he told his RM was for everything except the mine — and its
worst-of basket contains the same company. True exposure: **44.99% of his book**.

**The book-level scan found a client nobody was looking at.** CL-0014 sits
**0.59 points** from a margin call, tighter than either client we chose.

---

## Architecture

```
data/*.csv, rm_notes.json          read-only, never mutated
        │
   loader.py                       typed frames, one FX helper
        │
   GROUNDING ENGINE                6 analyzers, pandas only, zero LLM
        │
   build/facts.json                facts + the source rows they cite
        │
   ├── vault_build.py  →  vault/   Obsidian: Clients / Verified / Scenario / Sources
   ├── console/                    Vite + React: Book, Client, Fact drawer, Live assist
   ├── mcp_server/     :8848       5 tools — the voice layer's only route to a fact
   └── assist/         :8765       transcript in, grounded cue cards out
                                        ↑ Web Speech (primary) · Dograh (adapter)
```

The engine never calls a language model. The model's only jobs are playing a
client in Rehearse and choosing *which already-computed fact* to surface in
Assist.

### The analyzers

| | what it finds |
|---|---|
| `lookthrough` | True single-name exposure after expanding structured products, across every account |
| `collateral` | LTV recomputed from drawn ÷ lending value; breaches and cures attributed to client action or market move by counterfactual |
| `tension` | Where a documented objective contradicts what the book actually does |
| `liquidity` | What can genuinely be sold before a pledged facility breaches |
| `mandate` | Bands, single-position limits measured after look-through, binding exclusions |
| `triage` | All 20 clients ranked on seven weighted signals, weights published |

---

## Running it

```bash
make setup       # uv venv (Python 3.12) + dependencies
make build       # analyzers → build/facts.json
make test        # 177 tests

make vault       # → vault/ ; open the folder as an Obsidian vault
make console     # → localhost:5173
make assist      # → localhost:8765   live cue service
make mcp         # → localhost:8848   MCP server for the voice layer

make restart     # rebuild, resync console data, bounce both services
```

`vault/` and `build/` are generated and gitignored — a fresh clone has the
generators, not their output.

**Rehearse** needs [Dograh](https://github.com/dograh-hq/dograh) self-hosted. Point
an MCP tool at `http://host.docker.internal:8848/mcp`, attach four of the five
tools to the agent node (`list_clients` is deliberately excluded — the rehearsal
agent has no business enumerating other clients), and use
`mcp_server/personas/CL-0002_rehearse.md` as the prompt.

---

## What is and is not built

**Built:** the engine, the vault, the console, the MCP server, live Assist, and
the Rehearse persona. Two clients deep, twenty ranked. 28 facts, 43 cited source
rows, 77 vault notes, 177 tests.

**Not built:** `attribution.py` — decomposing a portfolio's move into the events
that caused it — and `scenario.py`, which would populate the Scenario folder with
Strait of Hormuz reopen/escalate analysis. The Scenario boundary exists and is
enforced; nothing has been written into it yet.

**Known limits.** The tension matcher folds word stems, which catches
"pharmaceutical" against "Kanto Pharma" but will always under-fire on synonyms it
has never seen. The resistance markers in `get_documented_objections` are a
keyword hint, not a classification — a note with no marker may still record a
client pushing back, and the tool says so. Retrieval is BM25, chosen because a
cue that arrives late in a live call is worse than no cue.

---

## Working on this with more than one person

The boundary is not by folder, because the thing worth protecting does not live
in one:

**Anything that constructs or emits a `Fact` is shared, invariant-protected
surface** — wherever it sits. An analyzer, an adapter around someone else's
computation, the vault builder, anything that renders fact text. Changes there
need both people aware, even when one person does the writing. A second route to
the screen that skips the invariants makes them decorative, and that has already
happened once: a scenario writer rendered notes straight from dicts, bypassing
both checks, and had to be replaced with an adapter.

**Everything else is single-owner** — rendering, styling, and API routes that
read facts without constructing them.

The line matters because a `Fact` is the only thing this system promises about.

---

## Documentation

- [`docs/anchorline-project-plan.md`](docs/anchorline-project-plan.md) — the problem and the product
- [`docs/anchorline-implementation-plan.md`](docs/anchorline-implementation-plan.md) — architecture and build sequence
- [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) — field reference for the dataset
- `vault/README.md` — the Verified/Scenario split, stated inside the vault

---

*Synthetic dataset prepared for SingHacks 2026. Not investment advice.*
