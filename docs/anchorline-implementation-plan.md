# Anchorline — Implementation Plan

**Companion to** `docs/anchorline-project-plan.md` (problem, use case, product).
**Constraints:** 32 hours wall-clock, solo + Claude Code, demo surface = Obsidian vault **and** a web console.

> **Every figure in this document was recomputed from the raw rows before the document was
> finalised** — independently, with stdlib `csv`, not read off the dataset's own derived columns.
> Where a shipped column exists (`weight_pct`, `ltv_pct_<date>`) the check diffed against it.
> Three figures confirmed to 4dp; one causal claim was **wrong and has been corrected** (§2.2).
> The verification script was standalone and has been deleted — Phase 2 rebuilds these as tests.

---

## 0. The one decision that shapes everything

With 32 hours and one builder, the failure mode is not "we ran out of ideas" — it's **three half-finished layers that never met each other**. So the build is sequenced around a **thin vertical slice first**: one client, five facts, rendered in the vault and in the console, by hour 5. Everything after that deepens a system that already works end to end.

The second shaping decision: **prose is never generated where a number is claimed.** The engine emits typed `Fact` objects whose headline text is *template-rendered from computed values*. The LLM's only jobs are (a) playing a client in Rehearse and (b) choosing *which already-computed fact* to surface in Assist. It never authors a number. This is what makes the governance claim in the project plan structurally true rather than a promise in a caveat.

---

## 1. Architecture

```
                          data/*.csv, rm_notes.json   (read-only, never mutated)
                                      │
                          ┌───────────▼────────────┐
                          │   loader.py            │  typed frames, date coercion,
                          │   + dq_report.py       │  data-quality artefact log
                          └───────────┬────────────┘
                                      │
                          ┌───────────▼────────────┐
                          │  GROUNDING ENGINE      │  7 deterministic analyzers
                          │  pure fns → Fact[]     │  pandas only, zero LLM
                          └───────────┬────────────┘
                                      │  build/facts.json   ← single source of truth
                 ┌────────────────────┼────────────────────┐
                 │                    │                    │
        ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼─────────┐
        │ vault_build.py  │  │  web console    │  │ anchorline-mcp   │
        │ Jinja → vault/  │  │  Vite+React     │  │ MCP server       │
        │ Verified/       │  │  reads facts    │  │ 5 grounded tools │
        │ Scenario/       │  │  + WS cues      │  └────────┬─────────┘
        │ Sources/        │  └────────▲────────┘           │
        └─────────────────┘           │                    │
              Obsidian                │            ┌───────▼────────┐
                                      │            │ Dograh (Docker)│
                              ┌───────┴──────┐     │ Rehearse agent │
                              │ FastAPI      │◄────┤ client persona │
                              │ /assist WS   │     └────────────────┘
                              └───────▲──────┘
                                      │ transcript
                     Web Speech API (primary) │ Dograh transcript (adapter)
```

### 1.1 Why the voice layer is split this way

Rehearse genuinely needs a talking AI, so it runs as a **Dograh agent** — browser Test Audio, persona built from that client's real `rm_notes` objections, grounded via our MCP tools.

Assist needs **no speech output at all** — it needs transcription in and text cue cards out. Building Assist on a raw transcript stream means:

- The "AI never speaks to the client" constraint is enforced by **the absence of an audio output path**, not by prompt instruction. That is a much stronger claim to make to a judge, and it's demonstrable.
- The riskiest external dependency (Dograh self-host behaving under demo pressure) is confined to *one* of the two modes. Assist works even if the Docker stack sulks.

Dograh is wired in as a *transcript adapter* for Assist, so when it's up, Assist runs on Dograh; when it isn't, the demo still runs. Same code path downstream of the transcript.

### 1.2 The Fact contract

Every claim in the entire product is one of these. Nothing renders that isn't one of these.

```python
class Source(BaseModel):
    file: str            # "holdings.csv"
    row_ref: str         # "PF-0003|SYN-ST-0103|2026-08-26"
    fields: list[str]    # ["market_value_usd", "weight_pct"]

class Fact(BaseModel):
    fact_id: str         # "F-CL0002-LOOKTHRU-001"
    client_id: str
    kind: Literal["attribution","lookthrough","mandate","collateral",
                  "liquidity","tension","scenario"]
    headline: str        # TEMPLATE-RENDERED. Never LLM prose.
    detail: str          # TEMPLATE-RENDERED.
    numbers: dict[str, float]
    sources: list[Source]        # non-empty — enforced by validator
    as_of: str
    confidence: Literal["verified","derived","scenario"]
    severity: int        # 0-100, feeds book triage
```

Two invariants, each with a test:

1. `len(fact.sources) > 0` for every fact — a fact with no source cannot be constructed.
2. Every number appearing in `headline`/`detail` appears in `numbers`. A regex extracts numerals from the rendered strings and asserts membership. This is the guardrail that makes the traceability claim mechanical.

### 1.3 Tech stack

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| Engine | Python 3.12, pandas, pydantic, `uv` | The dataset is 1,015 holdings rows. pandas is instant; a database is pure overhead at this size. **3.12 not 3.11:** the system default is 3.14.0, where pandas wheels are not reliably published, so `uv` fetches 3.12 for the venv. |
| Fact store | `build/facts.json` on disk | No DB, no migrations, no server for the console to depend on. Regenerating is `make build`. |
| Vault | Jinja2 → markdown files | Obsidian reads a folder. Nothing else needed. |
| Console | Vite + React + TS + Tailwind | Static build reading `facts.json`. Next.js buys SSR we don't need and costs config time we don't have. |
| Assist backend | FastAPI + WebSocket | One file. Needed only for the live cue stream. |
| Retrieval | BM25 over fact text (`rank_bm25`) | Deterministic, no embedding service, no cold start. At ~120 facts, semantic search is not the bottleneck; latency and predictability are. |
| Voice | Dograh, self-hosted Docker | Per the brief. MCP-native, which is exactly the integration we want. |
| Transcription | Browser Web Speech API | Zero infra, runs in the console that's already open. |

### 1.4 Repo layout

```
anchorline/
  engine/
    loader.py          dq_report.py       models.py
    analyzers/
      attribution.py   lookthrough.py     mandate.py
      collateral.py    liquidity.py       tension.py
      triage.py        scenario.py
    config/
      event_channels.yaml    # event → transmission channel → instrument selector
      scenarios.yaml         # Hormuz reopen / escalate shock definitions
    build.py           # runs all analyzers → build/facts.json
  vault_build.py       templates/*.j2
  mcp_server/server.py # 5 tools over facts.json
  assist/app.py        # FastAPI: WS /assist, POST /transcript
  console/             # Vite app
  vault/               # GENERATED — gitignored, rebuilt by make
  build/facts.json     # GENERATED
  Makefile             # make build | make vault | make serve | make all
  tests/               # invariants + golden numbers
```

---

## 2. The engine: seven analyzers

Ordered by demo value. Each emits `Fact[]`.

**1. `lookthrough.py`** — *the flagship.* Expands `instruments.underlying_reference` so a structured product reports what you're actually exposed to, then aggregates single-name exposure across all of a client's portfolios (including Custody, which no mandate report covers).

Two parsing requirements, both found by inspecting the actual `underlying_reference` strings:
- It must handle **three grammars**, not one: `Single underlying: X`, `Worst-of basket: X / Y / Z`, and `Underlying: X, <terms>`. A worst-of note carries downside to *every* name in the basket, so for concentration purposes the full notional counts against each — the conservative treatment, and the one a credit officer would recognise.
- It must **normalise instrument names before matching.** The dataset refers to the same company as `Pacific Orient Shipping` inside a basket string and `Pacific Orient Shipping Ltd` as an instrument name. Exact matching silently splits one exposure into two and *understates* concentration — precisely the failure this analyzer exists to prevent. This is one of the data-quality artefacts the brief says it planted; `dq_report.py` logs every fuzzy match it resolves so the join is auditable rather than magic.

> **Verified figures.** **CL-0002** holds Helios Cloud Systems directly at 14.0035% of PF-0003 *and* an Equity Linked Note on Helios at a further 10.6366% → **24.6400%** combined. Half of it is filed under `asset_class = "Structured Products"`, so no per-asset-class equity report shows it. Frame it as 24.64% **of PF-0003 specifically — the portfolio pledged as collateral for CF-0001** (it is only 7.80% of his total book, because the USD 31.92m Aranya Custody position dominates the denominator). The concentration sits exactly where the lending risk is, and that is the sentence Priscilla needs.

**2. `collateral.py`** — walks LTV across all five snapshots against `margin_call_ltv_pct`; classifies each breach and each cure as **market-driven** or **action-driven** using a counterfactual: recompute LTV holding `drawn` fixed, then holding `lending_value` fixed, and attribute to whichever effect dominates.

> **Verified, and this corrects an earlier draft of this plan.** **CF-0001** (CL-0002, collateral PF-0003): 63.3164 → 59.7213 → 61.6785 → **75.6372** → 73.7061 against a 75.0 trigger. Breached at 2026-06-30. The draft assumed the 2026-06-05 megacap drawdown was a contributing cause. **It was not.** Over Mar→Jun the counterfactual gives a draw effect of **+21.84pp** and a market effect of **−5.82pp** — lending value *rose* (7.78m → 8.59m); the market was pulling LTV **down**. The breach was caused solely by the client drawing an additional USD 1.7m (4,800,000 → 6,500,000, matching note N-004's "Drew a further USD 1.7m" to the dollar across two files).
>
> This is a better fact than the one it replaces, and it is what makes the Rehearse hard: N-004 records Priscilla warning him *at the time* — "I flagged that this increases his utilisation at exactly the moment his collateral is most volatile. He acknowledged the point but proceeded." The conversation is not "the market hurt you." It is "you were warned, you proceeded, and you breached." **Any analyzer that blames the market here is wrong, and the counterfactual is what stops it.**
>
> **CF-0005** (CL-0001): opens at 78.5 against a 70.0 trigger and is cured to 58.86 with `drawn` unchanged — cured by an event, not an action. The mirror image, and the pair makes the classifier's value obvious.

**3. `attribution.py`** — decomposes portfolio Δ between any two snapshots into per-instrument contribution, rolls up to asset class, then links to `event_log.csv` via a **hand-authored** `event_channels.yaml` mapping each event's `primary_transmission` to an instrument selector (asset class / sector / region / underlying reference), cross-checked against the matching `market_context` series.
> The mapping file is deliberately human-written and version-controlled: it is the auditable artefact that lets a reviewer see *why* the system linked an event to a holding. No LLM decides causation. Output reads like the data dictionary's own example: "down 4.1%, of which 3.3 points from duration as `UST_10Y_PCT` moved 4.05 → 4.66, partly offset by gold."

**4. `liquidity.py`** — matches `planned_cash_needs` + `commitments.uncalled` against what is genuinely sellable at `liquidity_tier == "Daily"`, net of positions pledged as facility collateral.
> **CL-0001** needs SGD 9m for a Bukit Timah deposit in H1-2027 while 98% of his Custody account is one Indonesian coal name that is also his family's operating business. **CL-0017** carries USD 15.8m uncalled across two funds with call windows opening 2026 Q4.

**5. `mandate.py`** — actual weights vs `min/max_pct` bands; `max_single_position_pct` applied only where `concentration_limit_applies == "Y"`; Custody portfolios excluded from mandate tests but included in the wealth picture; Sustainable Balanced exclusions checked against `sustainability_excluded`.
> **Accuracy note:** `transactions.csv` contains exactly one `Buy` and six `Structured Product Subscription` rows — it is an income/fee ledger, not a trade blotter. So "drift vs client-directed" is decided from subscriptions, facility drawdowns and `rm_notes`, **not** from reconstructed trading. The plan does not promise trade-based attribution the data can't support.

**6. `tension.py`** — **protected; do not cut.** Surfaces contradictions between what the client says (`objectives`, `life_stage`, `rm_notes`) and what the book does. Every tension must bind to a numeric data check; an LLM may propose candidate tensions from note text, but a candidate that fails its data check is dropped, not softened.

> **Verified, and sharper than the draft claimed.** **CL-0001**'s stated objective is to "diversify away from the family operating business." His Custody account is **97.9683%** Bara Nusantara Energy — the family coal business — which is **41.4156%** of his total book on its own.
>
> Then the look-through lands: in April he subscribed to `SYN-SP-0505`, a Fixed Coupon Note whose worst-of basket is *Pacific Orient Shipping / Global Energy Majors ADR / **Bara Nusantara Energy***. He bought a note that pays him less precisely when his own family's company falls. His true Bara exposure is **44.9853%** of his book (41.4156% direct + 3.5697% through the note) — and the note sits in PF-0001, the discretionary mandate he told Priscilla was meant to be *"the part of the family's wealth that is not tied to the mine"* (note N-001).
>
> That is the single best fact in this dataset: it requires joining `holdings` → `instruments.underlying_reference` → `rm_notes` → `clients.objectives`, it is invisible in every one of those files alone, it is fully traceable, and it is a conversation Priscilla genuinely cannot have without it. It is why this analyzer is protected.

**7. `triage.py`** — a transparent weighted score over the above (breach proximity, concentration excess, liquidity shortfall, days since contact, KYC overdue) producing the Monday-morning ranking of the book. Weights live in a config file and are shown in the UI, so the ranking is arguable rather than oracular.

> **Book-level concentration — near-free, and genuinely novel.** Once `lookthrough.py` exists, aggregating single names across *all 20 clients* costs one `groupby` and surfaces risk that appears on no client's report because it isn't on any one client's report: Priscilla's book carries **USD 11.95m of Helios** across CL-0002 and CL-0013 (via three different instruments), **USD 13.75m of Tanjong Global Macro** across five clients, and Bara Nusantara reaches **CL-0019** — a Gulf client with no Indonesian coal on his statement — through the same worst-of note. This is the "risk that is not on the report" at the level of the RM's whole book, and it is the cheapest high-value feature in the plan.

**`scenario.py`** (separate, and labelled separately) — applies shocks from `scenarios.yaml` (Hormuz reopens / escalates: Brent, TTF, gold, energy equities, shipping) to current holdings, revalues, and re-runs `collateral.py`. Every fact it emits carries `confidence="scenario"` and is written **only** to `vault/Scenario/`. The vault builder refuses to write a scenario fact into `Verified/`.

### Client depth: three, in this order

| | Client | Why this one |
|---|---|---|
| 1 | **CL-0002 Ravi Chandrasekaran** | Alone satisfies all five "what working looks like" criteria: 24.64% look-through concentration in the pledged portfolio, a real 75.64% breach he caused himself after being warned, and a documented objection ("won't sell listed tech before the Q4 secondary", N-003) that makes a genuinely hard Rehearse. **This is the demo client.** |
| 2 | **CL-0001 Hartono Wijaya Kusuma** | 44.99% true exposure to his own family's company — including through a note sitting in the account he said was for everything *except* the mine. Plus an event-cured breach and a 2027 SGD 9m liquidity need. **The single sharpest insight in the dataset.** |
| 3 | **CL-0014 Lau Chi Ming** *(candidate — not built)* | Surfaced by the book-level triage scan rather than by us: the **tightest facility in the entire book, 0.59pp from its margin-call trigger** — tighter than CL-0002's 1.29pp — plus a property developer whose largest holding is direct property. A stronger "the system found this, we weren't looking for it" moment than CL-0017. |
| — | **CL-0017 Fong Family Office** | Three portfolios, USD 15.8m uncalled. Now the fallback rather than the third client. |

Only one of these gets built, and only if hours 29–31 are genuinely clear.

Note that the demo client and the sharpest-insight client are *different people* — which is useful. CL-0002 carries the Rehearse and Assist story; CL-0001 carries the "you could not possibly have seen this" moment. Two clients, two different kinds of proof.

---

## 3. The 32 hours

Each phase has a **ship** line (non-negotiable) and a **cut** line (drop first if behind). Hours are elapsed from start.

### Phase 0 — Foundations · H0–H1.5
- Scaffold repo, `uv` env, Makefile, `.gitignore` for `vault/` and `build/`.
- `loader.py`: typed frames, date coercion, FX convention helper (`USDSGD` is SGD per USD — wrong division is the single easiest way to produce a confidently wrong number).
- `models.py`: `Fact`, `Source`, both invariant validators + their tests.
- **In parallel, start now:** `curl … start_docker.sh && ./start_docker.sh` for Dograh — first run takes 2–3 minutes and you want to know tonight, not at hour 18, whether it's healthy at `localhost:3010`.
- **Ship:** `make build` produces an empty but valid `facts.json`. **Cut:** nothing.

### Phase 1 — Vertical slice · H1.5–H5
One client (CL-0002), two analyzers (`lookthrough`, `collateral`), ~5 facts, rendered in *both* surfaces.
- `vault_build.py` writing `Clients/`, `Verified/`, `Sources/` with real wikilinks.
- Console shell: one client page, fact list, click-through drawer showing the source rows as a table.
- **Ship:** open Obsidian graph view on CL-0002 and every fact visibly traces to a source note. **Cut:** console styling — unstyled is fine here.
- **Checkpoint at H5:** if the slice isn't end to end, stop adding analyzers and finish it. Everything downstream assumes this works.

### Phase 2 — Engine depth · H5–H10
- `attribution.py` + `event_channels.yaml` (budget ~90 min for the mapping file alone — it is the audit artefact, write it carefully).
- `tension.py`, `liquidity.py`, `triage.py`, then `mandate.py`. **Written in that order**, because that is reverse cut order.
- Extend to CL-0001.
- **Golden-number tests, all pre-verified against raw rows** — these are known-good before a line of engine code exists:

  | Assertion | Value | Recomputed from |
  |---|---|---|
  | CL-0002 Helios in PF-0003 (direct + ELN) | `24.6400%` | `market_value_base / Σ portfolio` |
  | CL-0002 Helios direct / via ELN | `14.0035%` / `10.6366%` | same |
  | CF-0001 LTV series | `63.3164, 59.7213, 61.6785, 75.6372, 73.7061` | `drawn / lending_value × 100` |
  | CF-0001 breach driver, Mar→Jun | `CLIENT ACTION` (draw `+21.84pp`, market `−5.82pp`) | counterfactual |
  | CL-0001 Bara in PF-0002 (Custody) | `97.9683%` | `market_value_base / Σ portfolio` |
  | CL-0001 Bara true exposure, whole book | `44.9853%` (direct `41.4156` + look-through `3.5697`) | `market_value_usd`, worst-of basket expanded |
  | Book-level Helios across all clients | `USD 11,953,000` over 2 clients | look-through groupby |

- **Ship:** ~40 facts across two clients, all invariants green. **Cut:** `mandate.py` — band-vs-actual is the most conventional output here and the one a judge is least surprised by. `tension.py` is **protected**: it produces the CL-0001 finding above, which is the closest thing in this dataset to what the brief calls a winning answer.

### Phase 3 — Vault, properly · H10–H12
- Full generation, `Scenario/` folder scaffolded, `Sources/` notes for every referenced row, Obsidian graph legible for both clients.
- Write `vault/README.md` explaining the Verified/Scenario split — the compliance framing, stated inside the vault itself.
- **Ship:** `make vault` is idempotent and the graph view is screenshot-worthy.

### Sleep · H12–H18
Non-negotiable. The console and the voice layer both need judgement, and hour-26 judgement without sleep is where demos die.

### Phase 4 — MCP + Rehearse · H18–H22
- `mcp_server/server.py` exposing: `list_clients`, `get_client_brief`, `search_facts(client_id, query)`, `get_fact(fact_id)`, `get_documented_objections(client_id)`.
- Wire into Dograh (`/plugin marketplace add dograh-hq/dograh-plugins`, `/plugin install dograh@dograh`, then `/dograh-setup`).
- Build the Rehearse agent: persona = CL-0002 from his actual notes — agitated about the tech drawdown, wants to avoid selling before the Q4 secondary, comfortable increasing the Lombard line. He must push back, not roll over.
- **Ship:** a real spoken rehearsal in the browser where the AI-as-Ravi resists selling. **Cut:** the post-rehearsal scorecard.

### Phase 5 — Assist · H22–H25
- FastAPI: `POST /transcript` (chunk in) → BM25 over that client's facts → `WS /assist` (cue cards out), each cue carrying its `fact_id` and sources.
- Web Speech API capture in the console as the primary transcript source; Dograh transcript adapter behind the same endpoint.
- **Ship:** speak "he's worried about the collateral" into the console and the CF-0001 breach card appears, sourced. **Cut:** the Dograh adapter — Web Speech alone is a complete demo.

### Phase 6 — The console, made good · H25–H29
- Book view (triage table, weights visible), Client view (What changed / Why / Risks / Say this), Fact drawer, Live Assist, Rehearse launcher.
- This is the four hours where "world class" is earned: one type scale, one accent colour, generous whitespace, no gradient-on-card default look. Verified and Scenario get visually distinct, unmistakable treatments.
- **Ship:** the client page and the Assist view. **Cut:** the Rehearse launcher — launch Dograh in its own tab instead.

### Phase 7 — Scenario, guardrail, rehearse the demo · H29–H31.5
- `scenario.py` + Hormuz reopen/escalate written to `Scenario/`.
- **The guardrail demo:** deliberately feed the Assist LLM a fact set that doesn't support a number, and show the validator refusing to surface it. Ninety seconds, and it makes the governance claim concrete rather than asserted.
- CL-0017 only if genuinely clear.
- Run the full demo start to finish **three times.** Time it. Fix what breaks.

### H31.5–H32 — Submit
README, architecture diagram, submission form. Stop building.

---

## 4. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Dograh self-host misbehaves under demo pressure | Medium | Smoked at H0. Assist runs on Web Speech regardless; only Rehearse depends on it. Record a backup Rehearse video at H29. |
| Engine overruns and eats the console | High | Hard checkpoints at H5 and H10. `mandate.py` is the designated first cut; `tension.py` is protected and written early. |
| Instrument-name mismatch silently understates concentration | **High** | Confirmed present: `Pacific Orient Shipping` (basket string) vs `Pacific Orient Shipping Ltd` (instrument name). Normalise before matching; `dq_report.py` logs every fuzzy resolution. An unlogged fuzzy match is worse than no match. |
| Live speech recognition fails in the demo room (noise, network) | Medium | Assist accepts typed transcript on the same endpoint. A typed line is a legitimate fallback, not a broken demo. |
| Attribution linkage looks like hand-waving | Medium | `event_channels.yaml` is shown on screen. Human-authored, auditable, version-controlled — and that is the *point*, not an apology. |
| FX / bond-quantity convention errors produce confident wrong numbers | Medium | Convention helpers in `loader.py`, golden-number tests on known values, `dq_report.py` surfaces artefacts rather than silently coercing them. |
| Scope creep toward all 20 clients | Medium | Two clients deep is the stated plan. The triage view covers all 20 shallowly at near-zero cost, which is the right depth for the other eighteen. |

## 5. Validation

```bash
make build && make vault          # regenerate everything from scratch
pytest tests/ -q                  # invariants + golden numbers
python -m engine.dq_report        # data-quality artefacts, expected non-empty
python -m tests.check_vault_split # no scenario fact ever lands in Verified/
```

## 6. Done means

- [ ] CL-0002's portfolio explanation traces line by line to source rows, in the vault and the console
- [ ] Helios look-through concentration (24.6400% of the pledged portfolio) surfaced, with sources
- [ ] CF-0001's 75.6372% breach found, dated, and correctly attributed to **client action, not the market**
- [ ] CL-0001's 44.9853% true exposure to his own family's company surfaced, including the leg that runs through a worst-of note in the account he said was for everything except the mine
- [ ] A spoken rehearsal against AI-as-Ravi using his real documented objection
- [ ] A live cue card appearing, sourced, without being asked for
- [ ] The guardrail visibly refusing an unsupported number
- [ ] Verified and Scenario never blended, structurally
- [ ] Demo run end to end three times, timed
