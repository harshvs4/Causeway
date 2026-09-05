# Anchorline — Julius Baer Track, SingHacks 2026

Full problem/use-case/product plan: see `docs/anchorline-project-plan.md`. Read it before doing anything else.

## One-line summary
AI wealth-intelligence layer for RMs: explains portfolio changes with full traceability,
surfaces cross-portfolio risks, and helps the RM prep for and conduct client conversations
via voice — rehearsal and live in-call assist. The RM never lets the AI speak to a client
directly, at any point. This is a hard constraint, not a preference.

## Three layers
1. Grounding Engine — deterministic Python/pandas over the JB dataset. Never guesses;
   every fact traces to a real row in the data or event_log.csv.
2. Living Vault — Obsidian vault. Every client note wikilinks back to source data.
   Split into `Verified/` (only from event_log.csv and dataset rows) and
   `Scenario/` (explicitly labeled forward-looking hypotheticals). Never blend the two.
3. Voice Layer (Dograh, MCP-integrated) — two RM-facing modes only:
   - Rehearse: roleplay against an AI briefed on a specific client's real documented
     objections (from rm_notes.json)
   - Assist: listens during a real RM-client call, surfaces grounded facts live,
     never speaks to the client

## Non-negotiables
- No claim without a traceable source. Ever.
- AI never talks to a real client, at any point, in any mode.
- Depth over breadth — 2-3 clients done well, not 20 done shallow.

## Data
Dataset lives in `data/` — see docs/DATA_DICTIONARY.md for fields.
event_log.csv is authoritative for anything that happened in 2026 — never the model's
own knowledge of world events.