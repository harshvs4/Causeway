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

## Git workflow — never work on main

Every feature, enhancement, and bug fix happens on its own branch. This is standing
authorization: branch and push without asking each time.

1. **Before touching code, check the branch.** `git branch --show-current`. If it says
   `main`, stop and branch first — do not edit, do not commit.
2. **Branch per unit of work**, named for what it does:
   `feat/<slug>` · `fix/<slug>` · `chore/<slug>` · `docs/<slug>`
   (the repo already uses `merge/<slug>` for integration branches — keep that for merges
   of someone else's work).
3. **Push the branch to `origin`** (https://github.com/harshvs4/Causeway.git) once there
   is something worth pushing. Push early; don't sit on local-only commits.
4. **Merge to `main` only when it actually works** — `make build` succeeds and `make test`
   is fully green. A failing or skipped test is not "working fine"; say so rather than
   merging past it.
5. **Never force-push `main`**, and never rewrite history on a branch that has been pushed
   and shared.

If work has already begun on `main` by mistake, move it to a branch before committing
(`git switch -c <branch>` carries uncommitted changes across) rather than committing to
`main` and fixing it afterwards.