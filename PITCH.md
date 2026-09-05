# Anchorline — Pitch Deck Guide
### SingHacks 2026 · Julius Baer Wealth Intelligence Track

> Use this file as your source of truth when building slides.
> Every claim here is directly traceable to the built system.

---

## Slide 1 — Title

**Anchorline**
*AI wealth intelligence for relationship managers.*

Tagline options:
- "Every fact. Every source. Every call."
- "The RM knows. The client only hears the RM."
- "From dashboard to decision — in seconds."

---

## Slide 2 — The Problem (make it human)

**The RM's Monday morning.**

Priscilla Ong covers 20 clients — from an $8M individual to an $88M family office — alone.

Before every client meeting she has to manually:
1. Reconstruct *why* portfolios moved (connecting market events to specific holdings)
2. Spot risks that are invisible in any single portfolio
3. Decide who actually needs a call this week — across 20 people
4. Prepare for difficult conversations with clients who won't take advice

**None of this is supported by any existing tool.**
Dashboards show *what a portfolio is*. Not what happened. Not why. Not what to do.

> **The gap:** Descriptive data → Advisory intelligence.

---

## Slide 3 — Why This Is Hard (not just tedious)

The data that would make this possible is scattered across:
- 5 time snapshots per portfolio
- Market events with transmission channels
- Mandate rules (asset-class bands, concentration limits)
- Credit facilities with LTV history
- Planned cash needs, private market commitments
- RM notes — which often **contradict** the numbers (that's where the real advice lives)

And it requires restraint that is easy to get wrong:
- **Miss a real risk** → bad outcome for client
- **Overstate confidence** → regulatory and reputational risk for the bank

> A private bank cannot let an advisory tool free-associate about geopolitics or fabricate a plausible-sounding number in front of a client. That is not a nice-to-have. It is the central governance constraint.

---

## Slide 4 — The Insight

**The RM is not the bottleneck. Preparation is.**

Priscilla's job is the conversation — the human relationship, the judgment call, the difficult truth told with care. She is good at it.

What consumes her time is the hour before: manually synthesising data into a brief she already knows how to use.

> Anchorline doesn't replace the RM. It does the preparation so she doesn't have to.

**One non-negotiable design principle:**
The AI never speaks to the client. At any point. In any mode.
The client only ever hears Priscilla.

---

## Slide 5 — The Solution

**Three modes. One RM. Zero hallucinated numbers.**

| Mode | When | What it does |
|---|---|---|
| **Prep** | Before the meeting | Call queue ranked by severity. Client brief: what changed, why, what risk is building. Every claim linked to a source row. |
| **Rehearse** | Night before a hard conversation | Roleplay against an AI persona briefed on the client's real, documented objections. Practice before the stakes are real. |
| **Assist** | During the call (RM only) | Listens to the conversation. Surfaces the right fact at the right moment — silently. The client never knows. |

---

## Slide 6 — What We Built (the demo)

**Four fully working layers:**

### 1. Grounding Engine — Python/pandas, 1,800 lines
6 deterministic analyzers. No LLM. Every output traces to a real CSV row.

| Analyzer | What it finds |
|---|---|
| **Look-through** | Expands structured products to true exposure. CL-0002: 68% of their entire book is a single unlisted tech company — invisible from any single portfolio view. |
| **Collateral** | Tracks LTV across 5 snapshots. CF-0001 breach classified as 100% client-driven — the market was actually helping. |
| **Goal tension** | CL-0001 says "diversify from family business." Their actual exposure to that business is 45% — including through a worst-of note in the account specifically opened to escape it. |
| **Liquidity** | CL-0001 needs SGD 9m in future cash. 98% of their large custody account is illiquid coal business stock. |
| **Mandate** | Detects allocation band breaches after look-through. Classifies client-directed vs drift. |
| **Triage** | Weighted scoring of all 20 clients → Monday call queue. Weights: Collateral 25%, Liquidity 20%, Tension 18%, Concentration 16%, Mandate 12%, Contact 6%, KYC 3%. |

**Output:** 28 facts · 42 source rows · 20 clients ranked · 13 quotes verified against source

### 2. Signal Engine — stock-analyzer integration
- Wires technical indicator library (slope, momentum) into Causeway
- Computes for all 62 instruments: total return, recent return, period-by-period returns, trend direction
- Every number traces back to `instruments.csv` price columns

### 3. FastAPI Backend — 5 endpoints, live
- `GET /triage` — full call queue with top recommendation per client
- `GET /client/{id}` — facts + signals + RM notes + graph data
- `POST /voice-query` — grounded answer via BM25 keyword search. No LLM generation. Safe for voice integration.
- `GET /signals/{instrument_id}` — raw momentum signals
- `GET /health` — confirms 28 facts, 62 signals, 20 clients loaded

### 4. Console UI — Vite + React + Tailwind + React Flow
**Book view:**
- 20 clients ranked by severity score, colour-coded red/amber/green
- Signal weight legend with mini bar charts showing per-signal contribution per client

**Client drill-down:**
- Severity score header, 7 signal progress bars
- Facts grouped by kind, sorted by severity
- Each fact: severity badge, headline, detail, confidence tag (verified / derived / scenario)

**Attribution graph (React Flow):**
- Source CSV files → Fact kind nodes → Portfolio node
- Red animated edges for high-severity findings
- Draggable, dark theme

**Fact drawer:**
- Click any fact → see the exact CSV row(s) that produced it
- Cited fields highlighted in amber — full traceability in one click

**Voice Assist panel:**
- Floating panel on every client page
- Quick prompts: "What is the top risk?", "Why is liquidity flagged?", "What is the collateral status?"
- Free-text → calls `/voice-query` → grounded answer read aloud via browser TTS
- Dograh voice integration path: same `/voice-query` endpoint, already live

---

## Slide 7 — The Key Finding (demo centrepiece)

Pick **one** finding to walk through live. Recommended:

> **CL-0001 — The diversification that isn't.**
>
> The client's stated goal: "diversify away from the family business, Bara Nusantara Energy."
> Their actual exposure to BNE across all accounts: **44.99%** — including through a worst-of structured note purchased inside the account that was explicitly opened to escape the concentration.
>
> This is invisible in any single portfolio view. It only appears when you look through the structured product and combine both accounts.
>
> Anchorline finds this in seconds. Every number traces to a real row.

Walk through:
1. Call queue → CL-0001 ranked high → click
2. Goal Tension fact → click → fact drawer opens
3. Show the source row in `holdings.csv` highlighted in amber
4. Click Voice Assist → "What is the top risk?" → read aloud

---

## Slide 8 — The Governance Guarantee

This is what makes it deployable in a private bank, not just impressive in a demo.

**Two enforced invariants — not promises, structural constraints:**

1. **No unsourced claims.** Every Fact object requires ≥1 `Source` at construction. A fact without a source cannot exist in memory. It cannot reach the vault, the console, or a voice cue.

2. **No generated numbers.** Every numeral in rendered fact text must appear in the fact's `numbers` dict — validated by regex at save time. If a template tries to render a number that wasn't computed and declared, it fails loudly.

**Two structural separations:**

3. **Verified ≠ Scenario.** The vault enforces this at the file-system level. A scenario fact cannot be written to `Verified/`. A compliance reviewer can open the vault and know that everything in `Verified/` came from the data.

4. **AI never speaks to the client.** This is a hard architectural constraint, not a policy. The Assist mode has no audio output to the client. It surfaces cue cards to the RM only. The client only ever hears Priscilla.

---

## Slide 9 — Architecture (one diagram)

```
DATA LAYER                    ENGINE LAYER              PRESENTATION LAYER
─────────────────────         ──────────────────────    ─────────────────────────
clients.csv                   lookthrough.py            Console UI (React)
portfolios.csv    ──────────► collateral.py  ─────────► Call Queue (20 clients)
holdings.csv                  tension.py                Client Drill-down
instruments.csv               liquidity.py              Attribution Graph
mandates.csv                  mandate.py                Fact Drawer → CSV row
transactions.csv              triage.py
credit_facilities.csv         signals.py (new)         FastAPI Backend
market_context.csv                  │                   /triage
event_log.csv            ─────────► facts.json          /client/{id}
rm_notes.json                       │                   /voice-query
                            Obsidian Vault               /signals/{id}
                            Verified/ | Scenario/
                                                        Voice Assist Panel
                                                        Browser TTS (MVP)
                                                        Dograh (production)
```

---

## Slide 10 — Demo Flow (3 minutes)

1. **(30s)** Open console at `localhost:5173`. Show the call queue — 20 clients, ranked. Highlight that rank #1 has a score of 85, rank #20 is near zero. Explain the weights.

2. **(45s)** Click CL-0001 (Lau Chi Ming) or CL-0002 (Ravi Chandrasekaran) — whichever has the most dramatic finding. Show the signal bars, the facts grouped by kind.

3. **(45s)** Click the Goal Tension fact. Show the fact drawer — computed numbers on the left, the exact CSV row on the right, cited fields in amber. Say: "This is full traceability in one click. No hallucinated numbers."

4. **(30s)** Click "Show graph." Walk through the React Flow attribution graph — sources feed into fact kinds, fact kinds contribute to portfolio risk. Animated red edges = high severity.

5. **(30s)** Click "Voice Assist." Ask "What is the top risk?" → answer appears → click "Read aloud." Say: "This is the Dograh integration path — same endpoint, already live."

6. **(20s)** Close on the governance slide. "Every number traces to a row. The AI never talks to the client. This is deployable, not just impressive."

---

## Slide 11 — What's Next (if asked)

| Feature | Effort | Value |
|---|---|---|
| Dograh Rehearse mode (client persona roleplay) | 1 day | Highest — unique, no competitor has it |
| Live Assist WebSocket (real-time cue cards) | 1 day | High — the in-call use case |
| Scenario analyzer (Hormuz shock propagation) | 2 days | Medium — forward-looking layer |
| Signals view in console (momentum per holding) | 4 hours | Low — cosmetic enhancement |
| Production Dograh deployment (full voice stack) | 3 days | Needed for pilot |

---

## Slide 12 — One Sentence

> Anchorline gives relationship managers the brief they wish they'd always had — in seconds, with every number sourced, so the conversation they walk into is the only one that matters.

---

## Numbers to memorise

| Stat | Value |
|---|---|
| Clients in dataset | 20 |
| Total portfolio value tracked | ~$88M (largest single client) |
| Facts generated | 28 |
| Source rows verified | 42 |
| Instruments tracked | 62 |
| Triage signals | 7 |
| Lines of Python (engine) | ~2,100 |
| Lines of TypeScript (console) | ~800 |
| CL-0002 true Helios exposure | 68.35% of book |
| CL-0001 BNE exposure (stated goal: diversify) | 44.99% of book |
| CF-0001 LTV breach driver | 100% client-drawn (+USD 1.7m draw) |
| Time to demo from cold start | < 2 minutes (`make console-data && npm run dev`) |
