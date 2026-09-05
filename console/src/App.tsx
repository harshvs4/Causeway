import { useEffect, useState } from "react";
import type { Envelope, Fact } from "./types";
import Assist from "./Assist";
import Book from "./Book";
import Client from "./Client";
import FactDrawer from "./FactDrawer";
import "./styles.css";

type View = "book" | "client" | "assist";

const DOGRAH_URL = "http://localhost:3010";

export default function App() {
  const [data, setData] = useState<Envelope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("book");
  const [clientId, setClientId] = useState("CL-0002");
  const [selected, setSelected] = useState<Fact | null>(null);

  useEffect(() => {
    fetch("/facts.json")
      .then((r) => {
        if (!r.ok) throw new Error(`facts.json: HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error)
    return (
      <main className="canvas">
        <h1>Anchorline</h1>
        <p className="notice">
          {error} — run <code>make console-data</code>.
        </p>
      </main>
    );
  if (!data)
    return (
      <main className="canvas">
        <p className="empty">Loading…</p>
      </main>
    );

  const openClient = (id: string) => {
    setClientId(id);
    setView("client");
  };

  return (
    <div className="shell">
      <nav className="rail">
        <div className="wordmark">
          Anchorline
          <span>Wealth intelligence</span>
        </div>

        <div className="nav">
          <button aria-current={view === "book"} onClick={() => setView("book")}>
            Book
          </button>
          {data.clients.map((id) => {
            const row = data.triage.ranking.find((r) => r.client_id === id);
            return (
              <button
                key={id}
                aria-current={view === "client" && clientId === id}
                onClick={() => openClient(id)}
              >
                {row?.client_name.split(" ")[0] ?? id}
              </button>
            );
          })}
          <button aria-current={view === "assist"} onClick={() => setView("assist")}>
            Live assist
          </button>
          {/* Rehearse runs in Dograh's own browser session — it needs a
              microphone and a voice pipeline this console does not host. */}
          <a
            href={DOGRAH_URL}
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: "none" }}
          >
            <button style={{ width: "100%" }}>Rehearse ↗</button>
          </a>
        </div>

        <footer>
          {data.fact_count} facts · {Object.keys(data.source_rows).length} source rows
          <br />
          as of {data.as_of}
          <br />
          <br />
          Every claim traces to a row in the dataset.
        </footer>
      </nav>

      <main className="canvas">
        {view === "book" && <Book envelope={data} onOpenClient={openClient} />}
        {view === "client" && (
          <Client envelope={data} clientId={clientId} onOpenFact={setSelected} />
        )}
        {view === "assist" && <Assist clientId={clientId} />}
      </main>

      {selected && (
        <FactDrawer fact={selected} envelope={data} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
