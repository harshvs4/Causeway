# Anchorline

**AI wealth intelligence for relationship managers.**
SingHacks 2026 — Julius Baer track.

A private-bank RM can already see what a portfolio *is*. Anchorline explains what
**happened to it and why**, surfaces risk that no single report shows, and prepares
the RM for the conversation — before it, and during it.

Every claim traces to a row in the dataset. That is enforced by the code, not
promised in a caveat.

---

## Two rules, enforced structurally

**1. The AI never speaks to a client.** It prepares the RM and supports the RM, in
private. The client only ever hears their relationship manager.

Not a policy note — there is no audio output path anywhere in the live service.
There is no speaker to disable, and a test asserts no route can produce sound.
The rehearsal agent plays *the client*, against the RM, using that client's real
documented objections.

**2. No number is written where a number could be computed.** Every fact is a
typed object, and two invariants are enforced at construction — so a fact
violating either cannot exist in memory, let alone reach a vault, a screen, or a
voice agent:

- every fact carries at least one source;
- every number in its rendered text also appears in its computed values.

Verbatim quotations of source text are exempt from the second, and each quotation
is separately checked against the actual field value of a row the fact cites. An
unchecked quote would be a way to launder an invented number past the guardrail.

The engine never calls a language model. The model's only jobs are playing a
client in rehearsal, and choosing *which already-computed fact* to surface live.

---

## What it found

Two clients analysed in depth. Every figure below is reproducible from `data/` and
pinned as a test.

**Ravi Chandrasekaran (CL-0002)** holds Helios Cloud Systems directly at 14.00% of
his advisory portfolio *and* an equity-linked note on Helios at a further 10.64% —
**24.64% in one name**, half of it filed under "Structured Products" where no
per-asset-class report will show it. That portfolio is pledged as collateral. The
facility breached its margin-call trigger at **75.64%** against 75.00%, and the
counterfactual says this was **client action, not the market**: holding collateral
fixed, the drawdown moved LTV +21.84 points while the market was moving it −5.82.
He drew USD 1.7m days after his RM warned him — matching her note to the dollar,
across two files.

Of the USD 13,207,200 that looks daily-tradeable on his statement, **USD 240,726**
can actually be sold before that facility breaches.

**Hartono Wijaya Kusuma (CL-0001)** states an objective of diversifying away from
the family business. His custody account is **97.97%** that business. Then the
look-through lands: a fixed coupon note he subscribed to sits in the *other*
account — the one he told his RM was for everything except the mine — and its
worst-of basket contains the same company. True exposure: **44.99% of his book**.

**The book-level scan found a client nobody was looking at.** CL-0014 sits **0.59
points** from a margin call, tighter than either client we chose to study.

---

## Architecture

```
data/*.csv, rm_notes.json          read-only, never mutated
        │
   GROUNDING ENGINE                8 analyzers, pandas only, zero LLM
        │
   build/facts.json                facts + the source rows they cite
        │
   ├── vault/                      Obsidian: Clients / Verified / Scenario / Sources
   ├── console/                    Book · Client · Scenario · Fact drawer · Live assist
   ├── mcp_server/     :8848       5 tools — the voice layer's only route to a fact
   └── assist/         :8765       transcript in, grounded cue cards out
                                       ↑ Web Speech (primary) · Dograh (adapter)
```

| analyzer | what it finds |
|---|---|
| `lookthrough` | True single-name exposure after expanding structured products, across every account |
| `collateral` | LTV recomputed from drawn ÷ lending value; breaches and cures attributed to client action or market move by counterfactual |
| `attribution` | The dated event that reaches a holding, matched on transmission channel |
| `tension` | Where a documented objective contradicts what the book actually does |
| `liquidity` | What can genuinely be sold before a pledged facility breaches |
| `mandate` | Bands, single-position limits measured after look-through, binding exclusions |
| `scenario` | Strait of Hormuz escalate / reopen, shocked against today's holdings |
| `triage` | All 20 clients ranked on seven weighted signals, weights published |

---

## Running it

```bash
make setup       # uv venv (Python 3.12) + dependencies
make build       # analyzers → build/facts.json
make test        # 202 tests

make vault       # → vault/ ; open the folder as an Obsidian vault
make console     # → localhost:5173
make assist      # → localhost:8765   live cue service
make mcp         # → localhost:8848   MCP server for the voice layer
make restart     # rebuild, resync, bounce both services
```

`vault/` and `build/` are generated and gitignored — a fresh clone has the
generators, not their output.

Rehearsal needs [Dograh](https://github.com/dograh-hq/dograh) self-hosted. Point an
MCP tool at `http://host.docker.internal:8848/mcp`, attach four of the five tools
to the agent node — `list_clients` is deliberately excluded, since a rehearsal
agent has no business enumerating other clients — and use
`mcp_server/personas/CL-0002_rehearse.md` as the prompt.

---

## Where this stands

| | |
|---|---|
| Engine | 8 analyzers · **40 facts** · 65 cited source rows · 33 quotes verified against source |
| Clients | 2 deep, 20 ranked |
| Vault | 111 notes — 36 Verified, 4 Scenario, 67 Sources |
| Tests | **202 passing** |

**Scenario is isolated from every surface that answers "what is true"** — excluded
from live cue cards, spoken answers, and the search the rehearsal agent uses. It
stays reachable where its status is explicit: its own vault folder, its own visual
treatment, its own endpoint. A hypothesis should be hard to mistake for the
record, not merely labelled as one.

**There is no staged guardrail demo, deliberately.** A rehearsed refusal proves
nothing. The invariants caught three real defects during the final merge, each one
in the commit history:

- **The build refused to run.** A scenario label carrying a Brent price level was
  declared as a quotation; quote verification stopped the build, because that text
  appears in no cited row and would have exempted a numeral it had no right to.
- **A fact was true, sourced, verified — and still misleading.** A USD 4.2m tax
  obligation recorded as *"conditional on the sale completing"* kept that qualifier
  in its detail while the headline read as settled — and the console's "Say this"
  renders headlines alone. The one section designed to be spoken aloud would have
  had the RM state a contingency as a certainty. Provenance checking could never
  have caught it; reading the output did.
- **A docstring lied.** An endpoint claimed to exclude forward-looking facts and
  did not: asking whether the collateral was under pressure returned a Hormuz
  projection as flat text, with nothing marking it hypothetical.

**Known limits.** *Fact construction is airtight* — a fact cannot exist carrying a
number it did not compute. *Transcript checking is advisory* — it catches obvious
fabrications but matches on magnitude rather than meaning, so an invented figure
can collide with an unrelated real one. The two are not equally strong and should
not be claimed as such. The tension matcher folds word stems, catching
"pharmaceutical" against "Kanto Pharma" but always under-firing on unseen
synonyms. Retrieval is BM25, chosen because a cue arriving late in a live call is
worse than no cue.

---

## Credits

Built by **Harsh Sharma** and **@shekharsomani98**. The attribution and scenario
engines are @shekharsomani98's work, merged behind the shared `Fact` contract so
they carry the same traceability and vault treatment as the rest of the build.

- [`docs/anchorline-project-plan.md`](docs/anchorline-project-plan.md) — the problem and the product
- [`docs/anchorline-implementation-plan.md`](docs/anchorline-implementation-plan.md) — architecture and build sequence
- [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) — field reference for the dataset

*Synthetic dataset prepared for SingHacks 2026. Not investment advice.*
