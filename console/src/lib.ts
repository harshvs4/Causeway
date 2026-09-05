import type { Fact } from "./types";

export const money = (n: number) =>
  n.toLocaleString(undefined, { maximumFractionDigits: 0 });

export const pct = (n: number, dp = 2) => `${n.toFixed(dp)}%`;

export function severityClass(severity: number): string {
  if (severity >= 85) return "sev-high";
  if (severity >= 55) return "sev-mid";
  return "sev-low";
}

/** Verified and Scenario must be told apart at a glance, not by reading a label. */
export function confidenceChip(confidence: string) {
  if (confidence === "scenario") return { cls: "chip-scenario", label: "Scenario · not settled" };
  if (confidence === "verified") return { cls: "chip-verified", label: "Verified" };
  return { cls: "chip-derived", label: "Derived" };
}

export const isHypothesis = (fact: Fact) => fact.confidence === "scenario";

/**
 * The four questions an RM has before a meeting, answered from the facts we
 * actually hold. The mapping is stated in the UI rather than hidden, because a
 * section that silently omits a kind of finding is worse than one that says
 * what it covers.
 */
export const SECTIONS = [
  {
    id: "changed",
    title: "What changed",
    note: "Movement in the facility and the positions behind it, between the five snapshots.",
    kinds: ["collateral", "attribution"],
    pick: (f: Fact) => f.kind === "collateral" && !f.fact_id.includes("DRIVER"),
  },
  {
    id: "why",
    title: "Why",
    note: "Causation, attributed rather than assumed — and where the book contradicts what the client said they wanted.",
    kinds: ["collateral", "tension"],
    pick: (f: Fact) => f.kind === "tension" || f.fact_id.includes("DRIVER"),
  },
  {
    id: "risks",
    title: "Risks not on the report",
    note: "Exposure that only appears once you look through a wrapper, combine accounts, or ask what is genuinely sellable.",
    kinds: ["lookthrough", "liquidity", "mandate"],
    pick: (f: Fact) =>
      (f.kind === "lookthrough" || f.kind === "liquidity" || f.kind === "mandate") &&
      !f.fact_id.includes("INBAND"),
  },
  {
    id: "checked",
    title: "Checked and clear",
    note: "Tests that ran and found nothing. Stated so that silence always means \u201cnot checked\u201d rather than \u201cchecked, fine\u201d.",
    kinds: ["mandate"],
    pick: (f: Fact) => f.fact_id.includes("INBAND"),
  },
] as const;

/** Say this: the highest-severity findings, each a sentence you can say aloud
 *  because every figure in it was computed and is traceable. */
export function sayThis(facts: Fact[], n = 3): Fact[] {
  // At most one per kind. Severity alone picked two findings about the same
  // position - "this account is 100% one name" and "that name is 68% of the
  // book" - which is one point made twice. Spreading across kinds gives the RM
  // three different things to raise.
  const seen = new Set<string>();
  const out: Fact[] = [];
  for (const fact of [...facts].sort((a, b) => b.severity - a.severity)) {
    if (fact.kind === "triage" || fact.severity < 60) continue;
    if (seen.has(fact.kind)) continue;
    seen.add(fact.kind);
    out.push(fact);
    if (out.length === n) break;
  }
  return out;
}

export function bySection(facts: Fact[]) {
  const used = new Set<string>();
  return SECTIONS.map((section) => {
    const items = facts
      .filter((f) => f.kind !== "triage" && !used.has(f.fact_id) && section.pick(f))
      .sort((a, b) => b.severity - a.severity);
    items.forEach((f) => used.add(f.fact_id));
    return { ...section, items };
  });
}
