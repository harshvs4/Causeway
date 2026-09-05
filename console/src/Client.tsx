import type { Envelope, Fact } from "./types";
import { bySection, confidenceChip, isHypothesis, sayThis, severityClass } from "./lib";

function FactRow({ fact, onOpen }: { fact: Fact; onOpen: (f: Fact) => void }) {
  const chip = confidenceChip(fact.confidence);
  return (
    <button className={`fact ${severityClass(fact.severity)}`} onClick={() => onOpen(fact)}>
      <span className="bar" />
      <span className={isHypothesis(fact) ? "hypothesis" : ""}>
        <h4>{fact.headline}</h4>
        <p>{fact.detail}</p>
      </span>
      <span className="meta">
        <span className={`chip ${chip.cls}`}>{chip.label}</span>
        <div style={{ marginTop: "6px" }}>{fact.sources.length} source{fact.sources.length === 1 ? "" : "s"}</div>
      </span>
    </button>
  );
}

export default function Client({
  envelope,
  clientId,
  onOpenFact,
}: {
  envelope: Envelope;
  clientId: string;
  onOpenFact: (f: Fact) => void;
}) {
  const facts = envelope.facts.filter((f) => f.client_id === clientId);
  const triage = envelope.triage.ranking.find((r) => r.client_id === clientId);
  const sections = bySection(facts);
  const say = sayThis(facts);

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">
          {clientId} · ranked {triage?.rank} of {envelope.triage.ranking.length} this week
        </div>
        <h1>{triage?.client_name ?? clientId}</h1>
        <p className="lede">
          Everything below was computed from the dataset. Open any finding to see the
          figures behind it and the rows they came from.
        </p>
      </div>

      <div className="section">
        <header>
          <h3>Say this</h3>
          <span className="count">{say.length}</span>
        </header>
        <p className="section-note">
          The findings worth raising, in order. Each is a sentence you can say out loud,
          because every figure in it was computed and traces to a row.
        </p>
        <div className="record">
          {say.map((fact, i) => (
            <div key={fact.fact_id} style={{ marginBottom: "var(--s4)" }}>
              <div className="eyebrow">{i + 1}</div>
              <h3 style={{ marginBottom: "var(--s2)" }}>{fact.headline}</h3>
              <button onClick={() => onOpenFact(fact)}>Show the working</button>
            </div>
          ))}
        </div>
      </div>

      {sections.map((section) => (
        <div className="section" key={section.id}>
          <header>
            <h3>{section.title}</h3>
            <span className="count">{section.items.length}</span>
          </header>
          <p className="section-note">{section.note}</p>
          {section.items.length === 0 ? (
            <p className="empty">
              Nothing here yet — no analyzer currently produces facts of this kind.
            </p>
          ) : (
            <div className="facts">
              {section.items.map((fact) => (
                <FactRow key={fact.fact_id} fact={fact} onOpen={onOpenFact} />
              ))}
            </div>
          )}
        </div>
      ))}

      <div className="section">
        <header>
          <h3>Scenario</h3>
          <span className="count">0</span>
        </header>
        <div className="hypothesis" style={{ padding: "var(--s4)" }}>
          <span className="chip chip-scenario">Scenario · not settled</span>
          <p style={{ marginTop: "var(--s3)", marginBottom: 0, color: "var(--ink-2)" }}>
            Forward-looking analysis lives here and is kept structurally apart from the
            record above — different folder in the vault, different treatment on screen.
            None has been generated yet.
          </p>
        </div>
      </div>
    </>
  );
}
