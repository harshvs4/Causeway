import type { Envelope, Fact } from "./types";
import { sourceKey } from "./types";
import { confidenceChip, isHypothesis } from "./lib";

/** The end of the traceability chain: the claim, every number behind it, and
 *  the rows those numbers came from, with cited fields marked. */
export default function FactDrawer({
  fact,
  envelope,
  onClose,
}: {
  fact: Fact;
  envelope: Envelope;
  onClose: () => void;
}) {
  const chip = confidenceChip(fact.confidence);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={fact.headline}>
        <button className="close" onClick={onClose}>
          Close
        </button>

        <div className="eyebrow">{fact.fact_id}</div>
        <div className={isHypothesis(fact) ? "hypothesis" : "record"}>
          <h2>{fact.headline}</h2>
          <p style={{ color: "var(--ink-2)", marginTop: "var(--s3)" }}>{fact.detail}</p>
        </div>

        <p style={{ marginTop: "var(--s4)", display: "flex", gap: "var(--s2)", alignItems: "center" }}>
          <span className={`chip ${chip.cls}`}>{chip.label}</span>
          <span className="hint">
            {fact.kind} · severity {fact.severity} · as of {fact.as_of}
          </span>
        </p>

        <div className="section">
          <header>
            <h3>Computed values</h3>
            <span className="count">{Object.keys(fact.numbers).length}</span>
          </header>
          <div className="kv">
            {Object.entries(fact.numbers).map(([k, v]) => (
              <div key={k}>
                <span className="k">{k}</span>
                <span className="v">
                  {v.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                </span>
              </div>
            ))}
          </div>
          <p className="hint" style={{ marginTop: "var(--s3)" }}>
            Every figure in the text above appears here. That is enforced when the fact is
            built, not checked afterwards.
          </p>
        </div>

        <div className="section">
          <header>
            <h3>Sources</h3>
            <span className="count">{fact.sources.length}</span>
          </header>
          {fact.sources.map((source) => {
            const row = envelope.source_rows[sourceKey(source)] ?? {};
            const cited = new Set(source.fields);
            return (
              <div className="source-block" key={sourceKey(source)}>
                <div className="head">
                  <span className="file">{source.file}</span>
                  <span className="hint">{source.row_ref}</span>
                </div>
                <div className="kv">
                  {Object.entries(row).map(([k, v]) => (
                    <div key={k}>
                      <span className="k">{k}</span>
                      <span className={`v ${cited.has(k) ? "cited" : ""}`}>
                        {v === null || v === "" ? "—" : String(v)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
