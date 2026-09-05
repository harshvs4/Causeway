import type { Envelope, Fact } from "./types";

/**
 * Forward-looking analysis, kept visibly apart from the record.
 *
 * Everything here uses the hypothesis treatment — dashed ochre rule, diagonal
 * hatch, italic heading — so a scenario cannot be read as settled fact at a
 * glance. That is the same boundary the vault enforces with a separate folder
 * and the engine enforces by refusing to build a scenario fact with anything
 * other than confidence="scenario".
 */
export default function Scenario({
  envelope,
  clientId,
  onOpenFact,
}: {
  envelope: Envelope;
  clientId: string;
  onOpenFact: (f: Fact) => void;
}) {
  const facts = envelope.facts.filter(
    (f) => f.client_id === clientId && f.confidence === "scenario"
  );

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Scenario · {clientId}</div>
        <h1>If this happens</h1>
        <p className="lede">
          Conditions that have <strong>not occurred</strong>. These are shocks applied to
          today's holdings under stated rules — not forecasts, and not a statement about
          anything that happened. They are kept out of live cues and out of spoken answers
          entirely.
        </p>
      </div>

      {facts.length === 0 ? (
        <p className="empty">No scenario analysis generated for this client.</p>
      ) : (
        facts
          .sort((a, b) => b.severity - a.severity)
          .map((fact) => {
            const impact = fact.numbers.impact_usd ?? 0;
            const pct = fact.numbers.impact_pct ?? 0;
            const gains = fact.headline.includes("gains");
            return (
              <div className="section" key={fact.fact_id}>
                <div className="hypothesis" style={{ padding: "var(--s5)" }}>
                  <span className="chip chip-scenario">Scenario · not settled</span>
                  <h2 style={{ margin: "var(--s3) 0 var(--s3)", fontStyle: "italic" }}>
                    {fact.headline}
                  </h2>
                  <p style={{ color: "var(--ink-2)", fontSize: "var(--t-small)" }}>
                    {fact.detail}
                  </p>

                  <div className="weights" style={{ marginTop: "var(--s5)" }}>
                    <div className="weight">
                      <div className="k">{gains ? "Gain" : "Loss"}</div>
                      <div className="v">{impact.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
                    </div>
                    <div className="weight">
                      <div className="k">of the book</div>
                      <div className="v">{pct.toFixed(2)}%</div>
                    </div>
                    <div className="weight">
                      <div className="k">Assumed Brent</div>
                      <div className="v">
                        {fact.numbers.assumed_brent_usd?.toFixed(0) ?? "—"}
                      </div>
                    </div>
                    <div className="weight">
                      <div className="k">Facilities flagged</div>
                      <div className="v">{fact.numbers.facilities_flagged ?? 0}</div>
                    </div>
                  </div>

                  <p style={{ marginTop: "var(--s5)", marginBottom: 0 }}>
                    <button onClick={() => onOpenFact(fact)}>Show the working</button>
                  </p>
                </div>
              </div>
            );
          })
      )}
    </>
  );
}
