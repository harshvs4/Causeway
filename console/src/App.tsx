import { useEffect, useState } from "react";
import type { Envelope, Fact, Source } from "./types";
import { sourceKey } from "./types";
import Assist from "./Assist";

/**
 * Phase 1 console shell. Deliberately unstyled - styling is the designated cut
 * and Phase 6 is where "world class" gets earned. What has to work now is the
 * traceability path: client -> fact -> the actual row.
 */
export default function App() {
  const [data, setData] = useState<Envelope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Fact | null>(null);
  const [view, setView] = useState<"facts" | "assist">("facts");

  useEffect(() => {
    fetch("/facts.json")
      .then((r) => {
        if (!r.ok) throw new Error(`facts.json: HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <main><h1>Anchorline</h1><p>{error} — run <code>make console-data</code>.</p></main>;
  if (!data) return <main><h1>Anchorline</h1><p>Loading…</p></main>;

  const byKind = data.facts.reduce<Record<string, Fact[]>>((acc, f) => {
    (acc[f.kind] ||= []).push(f);
    return acc;
  }, {});

  return (
    <main>
      <h1>Anchorline</h1>
      <p>
        {data.fact_count} facts · as of {data.as_of} · built {data.generated_at}
      </p>
      <p>
        <button onClick={() => setView("facts")} disabled={view === "facts"}>
          Facts
        </button>{" "}
        <button onClick={() => setView("assist")} disabled={view === "assist"}>
          Live assist
        </button>
      </p>

      {view === "assist" && <Assist clientId="CL-0002" />}

      {view === "facts" && Object.entries(byKind).map(([kind, facts]) => (
        <section key={kind}>
          <h2>{kind}</h2>
          <ul>
            {facts
              .sort((a, b) => b.severity - a.severity)
              .map((f) => (
                <li key={f.fact_id}>
                  <button onClick={() => setSelected(f)}>{f.headline}</button>{" "}
                  <small>
                    [{f.confidence} · severity {f.severity} · {f.sources.length} source
                    {f.sources.length === 1 ? "" : "s"}]
                  </small>
                </li>
              ))}
          </ul>
        </section>
      ))}

      {view === "facts" && selected && (
        <FactDrawer fact={selected} envelope={data} onClose={() => setSelected(null)} />
      )}
    </main>
  );
}

function FactDrawer({
  fact,
  envelope,
  onClose,
}: {
  fact: Fact;
  envelope: Envelope;
  onClose: () => void;
}) {
  return (
    <aside>
      <hr />
      <button onClick={onClose}>close</button>
      <h2>{fact.headline}</h2>
      <p>{fact.detail}</p>

      <h3>Computed values</h3>
      <table>
        <tbody>
          {Object.entries(fact.numbers).map(([k, v]) => (
            <tr key={k}>
              <td><code>{k}</code></td>
              <td>{v.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Sources</h3>
      {fact.sources.map((s) => (
        <SourceRow key={sourceKey(s)} source={s} envelope={envelope} />
      ))}
    </aside>
  );
}

function SourceRow({ source, envelope }: { source: Source; envelope: Envelope }) {
  const row = envelope.source_rows[sourceKey(source)];
  if (!row) return <p>Missing source row for {sourceKey(source)}</p>;
  const cited = new Set(source.fields);

  return (
    <div>
      <h4>
        <code>{source.file}</code> — {source.row_ref}
      </h4>
      <table>
        <tbody>
          {Object.entries(row).map(([k, v]) => (
            <tr key={k}>
              <td>{cited.has(k) ? <strong>{k}</strong> : k}</td>
              <td>{v === null ? "" : String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
