import type { Envelope, TriageRow } from "./types";

const SIGNALS = ["collateral", "liquidity", "tension", "concentration", "mandate", "contact", "kyc"];

/** Monday morning: twenty clients, one relationship manager, ranked — with the
 *  weights on screen so the ranking can be argued with rather than trusted. */
export default function Book({
  envelope,
  onOpenClient,
}: {
  envelope: Envelope;
  onOpenClient: (clientId: string) => void;
}) {
  const { weights, ranking } = envelope.triage;
  const deep = new Set(envelope.clients);
  const top = ranking[0]?.score ?? 100;

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Book · {ranking.length} clients · as of {envelope.as_of}</div>
        <h1>Who needs a call first</h1>
        <p className="lede">
          Every client scored on the same seven signals. Two have been analysed in depth;
          the rest are covered by signals cheap enough to run across the whole book — which
          is how the tightest facility in it was found.
        </p>
      </div>

      <div className="section">
        <header>
          <h3>Weights</h3>
          <span className="count">sum 100</span>
        </header>
        <p className="section-note">
          The score is an ordering device, not a probability. These weights live in
          <code> engine/config/triage_weights.yaml</code> with the reasoning written beside
          each one.
        </p>
        <div className="weights">
          {Object.entries(weights).map(([name, value]) => (
            <div className="weight" key={name}>
              <div className="k">{name}</div>
              <div className="v">{value}</div>
              <div className="track">
                <div className="fill" style={{ width: `${(value / 25) * 100}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="section">
        <header>
          <h3>Ranking</h3>
          <span className="count">{ranking.length}</span>
        </header>
        <table>
          <thead>
            <tr>
              <th style={{ width: "26px" }}>#</th>
              <th>Client</th>
              <th>Signals</th>
              <th className="num" style={{ width: "150px" }}>Score</th>
              <th style={{ width: "84px" }} />
            </tr>
          </thead>
          <tbody>
            {ranking.map((row: TriageRow) => {
              const isDeep = deep.has(row.client_id);
              return (
                <tr key={row.client_id} className={isDeep ? "deep" : "dimmed"}>
                  <td className="num">{row.rank}</td>
                  <td>
                    {row.client_name}
                    <div className="hint">{row.client_id}</div>
                  </td>
                  <td>
                    <div className="signals" title={SIGNALS.join(" · ")}>
                      {SIGNALS.map((s) => {
                        const v = row.signals[s] ?? 0;
                        return <i key={s} className={v >= 70 ? "hot" : v > 0 ? "on" : ""} />;
                      })}
                    </div>
                  </td>
                  <td>
                    <div className="score">
                      <div className="track">
                        <div className="fill" style={{ width: `${(row.score / top) * 100}%` }} />
                      </div>
                      <span className="value">{row.score.toFixed(1)}</span>
                    </div>
                  </td>
                  <td>
                    {isDeep ? (
                      <button onClick={() => onOpenClient(row.client_id)}>Open</button>
                    ) : (
                      <span className="hint">signals only</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="hint" style={{ marginTop: "var(--s4)" }}>
          Filled marks are signals firing, in weight order: {SIGNALS.join(" · ")}. Accent
          marks are above 70.
        </p>
      </div>
    </>
  );
}
