# Anchorline
### AI Wealth Intelligence for Relationship Managers
**SingHacks 2026 — Julius Baer Track: Wealth Intelligence**

---

## 1. The Problem

### 1.1 Where things stand today

A private-bank Relationship Manager (RM) can already open a dashboard and see a client's portfolio valuation, performance, and allocation. That data is accurate, current, and completely useless on its own — because it tells the RM *what a portfolio is*, not *what happened to it or why*, and not *what to do next*.

Priscilla Ong, the RM at the center of this dataset, covers 20 clients alone — from an $8M individual to an $88M multi-generational family office. That's a realistic single-RM book. Before every client meeting, she has to manually:

- Reconstruct why a portfolio moved the way it did, by connecting market/geopolitical events to specific holdings
- Spot risks that are often invisible in any single portfolio and only appear once you combine a client's accounts, or look through a structured product to what it's actually exposed to
- Decide, across 20 people, who genuinely needs a call this week and who doesn't
- Prepare for the actual human part of the job — the conversation itself, especially the hard ones (a client who won't sell at a loss, a client whose stated goals no longer match their allocation)

None of this is supported by existing tools. It's manual synthesis, done from memory and intuition, repeated for every client, every week.

### 1.2 Why this is hard, not just tedious

The data that would let someone do this well is scattered across formats that don't talk to each other: five time snapshots per portfolio, a market-context table, an event log, mandate rules, credit facilities, planned cash needs, and Priscilla's own informal notes — which sometimes *contradict* the numbers, which is often exactly where the real advice lives.

Doing this synthesis well also requires restraint that's easy to get wrong in either direction: miss a real risk, or worse, state something with more confidence than the data supports. A bank cannot let an advisory tool free-associate about geopolitics or fabricate a plausible-sounding explanation in front of a client. That's not a nice-to-have — it's the central governance constraint the brief itself calls out.

### 1.3 Why now

Portfolios are getting more complex — more asset classes, more jurisdictions, more structured products whose real exposure isn't obvious from their label — while the number of clients an RM is expected to personally track hasn't gone down. The gap between "descriptive dashboard" and "advisory intelligence" is the gap this challenge is asking us to close.

---

## 2. The Use Case

### 2.1 Who this is for

The primary user is the RM — not the client. Every part of this product is designed around one non-negotiable principle: **the client only ever hears from Priscilla. The AI never talks to the client directly, at any point.** More on why in Section 3.4 — this isn't a limitation we're working around, it's a deliberate design commitment that matches how the brief itself describes the RM's role.

### 2.2 A day with Anchorline

**Before the meeting — Rehearse.**
Priscilla has a call with a client tomorrow who's told her, more than once, that he won't sell his bond position at a loss. She knows the conversation will be difficult. Instead of walking in cold, she opens Anchorline and talks through the conversation with an AI that's been briefed to argue back exactly the way that client actually does — using his real, documented objections. She practices her opening, gets pushed back on, and adjusts before the stakes are real.

**Right before the meeting — Prep.**
She opens the client's page. Instead of a wall of numbers, she sees what changed, why (with every claim traceable to a real event and a real row of data, not an AI's best guess), what risk is building that isn't obvious from either of his two portfolios alone, and a ranked view of her whole book so she knows this client actually deserves today's slot.

**During the meeting — Assist.**
She's on the call. She's the only one talking. In the background, the system is listening and quietly surfacing exactly the fact she needs, exactly when the conversation reaches it — a number, a risk, a reason — so she never has to break the conversation's flow to go look something up. The client never knows the system exists. Priscilla decides, in the moment, whether to use what it shows her.

**After the meeting.**
The vault updates. What was discussed, what was decided, what's now known about this client becomes part of the record — searchable, linked, and available the next time this client comes up, whether that's in three days or three months.

### 2.3 The shift this represents

From: *"What does my client's portfolio look like?"*
To: *"What should I know, what should I say, and how do I say it well?"*

That third piece — *how do I say it well* — is the part existing tools don't touch at all, and it's the part this use case is actually built around.

---

## 3. What We're Building

### 3.1 One-line definition

**Anchorline is an RM intelligence layer that explains what happened in a client's portfolio and why, surfaces risks a static dashboard can't see, and prepares the RM — through both rehearsal and live in-conversation support — to have the actual human conversation well.**

### 3.2 The three functional pieces

**A. The Grounding Engine**
The analytical core. It reads the full five-snapshot dataset and produces the facts everything else depends on: what changed in a portfolio and by how much, which specific real-world events plausibly explain it, which risks only appear when you combine a client's multiple portfolios or look through a structured product to its true underlying exposure, where a portfolio sits outside its mandate and whether that's drift or a client-directed choice, whether pledged collateral is trending toward a problem, and whether a client's stated future needs are actually supported by what's currently sellable in their book.

This layer never guesses. Every fact it produces is traceable to a specific row in the dataset or a specific entry in the event log — nothing here is generated prose standing in for evidence.

**B. The Living Vault**
Where the Grounding Engine's output becomes something a person can actually explore, and where the governance constraint gets enforced structurally rather than just promised in a caveat. Every client has a running record. Every claim in it links to exactly the data it came from. And the record is split into two kinds of knowledge that are never allowed to blur into each other:

- **Verified** — what actually happened, sourced only from the dataset and the event log. This is the record an RM can put in front of a compliance reviewer.
- **Scenario** — what *might* happen next (for instance, how a client's book would be affected if the ongoing Middle East situation escalates or de-escalates). Explicitly labeled as forward-looking and hypothetical, kept structurally separate so it's never mistaken for settled fact.

This is also, functionally, the audit trail — you can start from any insight and trace it back to exactly what produced it.

**C. The Voice Layer — two modes, one purpose**
Both modes exist to help Priscilla have the actual conversation well. Neither one ever talks to a client.

- **Rehearse** — Before a difficult conversation, Priscilla can practice it against an AI briefed to respond the way that specific client actually does, using their real documented positions and objections. She gets to make her mistakes in private, before the meeting that matters.
- **Assist** — During a real conversation, the system listens and surfaces the relevant grounded fact at the moment the conversation needs it — a number, a risk, a piece of context — without ever taking the conversation over. Priscilla remains the only voice the client hears, and the only person deciding what gets said.

### 3.3 How the three pieces connect

This is one product telling one story, not three features bolted together. The Grounding Engine produces the facts. The Vault holds them, connects them, and makes them inspectable. The Voice Layer puts them in Priscilla's hands at the two moments they matter most — before the conversation and during it. Every mode draws from the same underlying record, so what she rehearses against, what she reads in prep, and what gets surfaced live are always consistent with each other.

### 3.4 The governance principle, stated plainly

Private banking's value proposition *is* the human relationship. The brief says this outright — the RM stays central, recommendations support human decision-making rather than replace it, and every insight has to be something an RM can actually stand behind in front of a client. An AI that talks to clients directly, unsupervised, works against that principle no matter how well it's built. So Anchorline draws the line deliberately: the AI prepares Priscilla and supports Priscilla, in private, before and during the conversation — and the client only ever hears Priscilla.

### 3.5 What this explicitly does not do

- The AI never contacts, calls, or speaks to a client at any point.
- The AI never issues a recommendation without it being traceable to specific source data.
- The system does not attempt to cover all 20 clients with equal depth — the plan is to go deep on a small number of clients whose stories are genuinely well understood, not wide across all of them.
- Nothing in the Scenario layer is ever presented as settled fact.

---

## 4. What "Working" Looks Like

By the end of this build, Anchorline should be able to:

1. Take a specific client and produce a portfolio explanation that a compliance reviewer could trace, line by line, back to real data — with nothing invented.
2. Surface at least one risk that would be genuinely invisible without combining data across files or looking through a structured product.
3. Let Priscilla rehearse a real, difficult conversation against an AI briefed on that specific client's documented position.
4. During a live (simulated) conversation, surface the right grounded fact at the right moment without Priscilla having to ask for it.
5. Make every one of the above inspectable — nothing in the product should be a claim you can't immediately trace back to its source.

---

*Implementation plan, architecture, and build sequence to follow separately.*