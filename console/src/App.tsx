import { useEffect, useState, useCallback, useRef } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
} from "@xyflow/react";
import type { Envelope, Fact, TriageRow, Source } from "./types";
import { sourceKey } from "./types";

// ─── colour helpers ────────────────────────────────────────────────────────────

function scoreColor(score: number) {
  if (score >= 75) return "text-red-400";
  if (score >= 50) return "text-amber-400";
  return "text-emerald-400";
}


function confidenceBadge(c: string) {
  if (c === "verified") return "bg-emerald-900 text-emerald-300 border border-emerald-700";
  if (c === "derived") return "bg-blue-900 text-blue-300 border border-blue-700";
  return "bg-violet-900 text-violet-300 border border-violet-700";
}

function severityBadge(s: number) {
  if (s >= 80) return "bg-red-900 text-red-300";
  if (s >= 50) return "bg-amber-900 text-amber-300";
  return "bg-slate-700 text-slate-300";
}

const KIND_LABELS: Record<string, string> = {
  lookthrough: "Look-through",
  collateral: "Collateral",
  tension: "Goal Tension",
  liquidity: "Liquidity",
  mandate: "Mandate",
  triage: "Triage",
};

const SIGNAL_COLORS: Record<string, string> = {
  collateral: "bg-red-500",
  liquidity: "bg-orange-500",
  tension: "bg-amber-500",
  concentration: "bg-yellow-500",
  mandate: "bg-lime-600",
  contact: "bg-sky-500",
  kyc: "bg-violet-500",
};

// ─── main app ─────────────────────────────────────────────────────────────────

export default function App() {
  const [data, setData] = useState<Envelope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeClient, setActiveClient] = useState<string | null>(null);
  const [activeFact, setActiveFact] = useState<Fact | null>(null);

  useEffect(() => {
    fetch("/facts.json")
      .then((r) => {
        if (!r.ok) throw new Error(`facts.json: HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  const openClient = useCallback((id: string) => {
    setActiveClient(id);
    setActiveFact(null);
  }, []);

  const goBack = useCallback(() => {
    setActiveClient(null);
    setActiveFact(null);
  }, []);

  if (error) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center">
        <div className="text-center space-y-2">
          <p className="text-red-400 font-mono">{error}</p>
          <p className="text-slate-500 text-sm">
            Run <code className="bg-slate-800 px-1 rounded">make console-data</code> then refresh.
          </p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-400 flex items-center justify-center">
        <p className="animate-pulse font-mono text-sm">Loading facts…</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* header */}
      <header className="border-b border-slate-800 px-6 py-3 flex items-center gap-4">
        <button
          onClick={goBack}
          className="font-semibold tracking-wide text-white hover:text-amber-400 transition-colors"
        >
          Anchorline
        </button>
        {activeClient && (
          <>
            <span className="text-slate-600">/</span>
            <span className="text-slate-400 text-sm">
              {data.triage.ranking.find((r) => r.client_id === activeClient)?.client_name ?? activeClient}
            </span>
          </>
        )}
        <div className="ml-auto text-xs text-slate-600 font-mono">
          {data.fact_count} facts · as of {data.as_of}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* main content */}
        <main className="flex-1 overflow-auto">
          {activeClient ? (
            <ClientPage
              clientId={activeClient}
              envelope={data}
              activeFact={activeFact}
              onFactSelect={setActiveFact}
              onBack={goBack}
            />
          ) : (
            <TriageTable ranking={data.triage.ranking} weights={data.triage.weights} onSelect={openClient} />
          )}
        </main>

        {/* fact drawer */}
        {activeFact && (
          <FactDrawer fact={activeFact} envelope={data} onClose={() => setActiveFact(null)} />
        )}
      </div>
    </div>
  );
}

// ─── triage table ─────────────────────────────────────────────────────────────

function TriageTable({
  ranking,
  weights,
  onSelect,
}: {
  ranking: TriageRow[];
  weights: Record<string, number>;
  onSelect: (id: string) => void;
}) {
  const signals = Object.keys(weights);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold">Call Queue</h1>
        <span className="text-slate-500 text-sm">{ranking.length} clients · ranked by severity</span>
      </div>

      {/* signal weight legend */}
      <div className="flex flex-wrap gap-2">
        {signals.map((sig) => (
          <span key={sig} className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className={`w-2 h-2 rounded-full ${SIGNAL_COLORS[sig] ?? "bg-slate-500"}`} />
            {sig} {weights[sig]}%
          </span>
        ))}
      </div>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-900 text-slate-400 text-xs uppercase tracking-wider">
              <th className="px-4 py-3 text-left w-12">#</th>
              <th className="px-4 py-3 text-left">Client</th>
              <th className="px-4 py-3 text-right w-24">Score</th>
              <th className="px-4 py-3 text-left">Signal breakdown</th>
            </tr>
          </thead>
          <tbody>
            {ranking.map((row) => (
              <tr
                key={row.client_id}
                onClick={() => onSelect(row.client_id)}
                className="border-t border-slate-800 hover:bg-slate-900/60 cursor-pointer transition-colors group"
              >
                <td className="px-4 py-3 text-slate-500 font-mono text-xs">{row.rank}</td>
                <td className="px-4 py-3">
                  <div className="font-medium group-hover:text-amber-400 transition-colors">
                    {row.client_name}
                  </div>
                  <div className="text-slate-600 text-xs font-mono">{row.client_id}</div>
                </td>
                <td className="px-4 py-3 text-right">
                  <span className={`font-mono font-bold text-base ${scoreColor(row.score)}`}>
                    {row.score.toFixed(0)}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <SignalBreakdown signals={row.signals} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SignalBreakdown({ signals }: { signals: Record<string, number> }) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {Object.entries(signals)
        .filter(([, v]) => v > 0)
        .sort(([, a], [, b]) => b - a)
        .map(([key, val]) => (
          <div key={key} className="flex flex-col items-center gap-0.5" title={`${key}: ${val}`}>
            <div className="h-5 w-5 rounded-sm overflow-hidden bg-slate-800">
              <div
                className={`w-full ${SIGNAL_COLORS[key] ?? "bg-slate-500"} transition-all`}
                style={{ height: `${val}%` }}
              />
            </div>
            <span className="text-[9px] text-slate-600 font-mono">{key.slice(0, 3)}</span>
          </div>
        ))}
    </div>
  );
}

// ─── client page ──────────────────────────────────────────────────────────────

function ClientPage({
  clientId,
  envelope,
  activeFact,
  onFactSelect,
  onBack,
}: {
  clientId: string;
  envelope: Envelope;
  activeFact: Fact | null;
  onFactSelect: (f: Fact) => void;
  onBack: () => void;
}) {
  const [showGraph, setShowGraph] = useState(false);
  const triageRow = envelope.triage.ranking.find((r) => r.client_id === clientId);
  const facts = envelope.facts.filter((f) => f.client_id === clientId);

  const byKind = facts.reduce<Record<string, Fact[]>>((acc, f) => {
    (acc[f.kind] ||= []).push(f);
    return acc;
  }, {});

  // sort kinds: highest max-severity first
  const sortedKinds = Object.entries(byKind).sort(
    ([, a], [, b]) => Math.max(...b.map((f) => f.severity)) - Math.max(...a.map((f) => f.severity))
  );

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* client header */}
      <div className="flex items-start gap-4">
        <button onClick={onBack} className="mt-1 text-slate-500 hover:text-slate-200 transition-colors text-sm">
          ← back
        </button>
        <div className="flex-1">
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-semibold">{triageRow?.client_name ?? clientId}</h1>
            <span className="font-mono text-slate-500 text-sm">{clientId}</span>
          </div>
          {triageRow && (
            <div className="flex items-center gap-3 mt-2">
              <span
                className={`text-3xl font-bold font-mono ${scoreColor(triageRow.score)}`}
              >
                {triageRow.score.toFixed(0)}
              </span>
              <span className="text-slate-500 text-sm">severity score</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-500 text-sm">rank #{triageRow.rank}</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-500 text-sm">{facts.length} facts</span>
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGraph((v) => !v)}
            className={`px-3 py-1.5 rounded text-xs border transition-colors ${
              showGraph
                ? "bg-amber-900/30 border-amber-700 text-amber-300"
                : "border-slate-700 text-slate-400 hover:border-slate-500"
            }`}
          >
            {showGraph ? "hide graph" : "show graph"}
          </button>
          <VoicePanel clientId={clientId} clientName={triageRow?.client_name ?? clientId} />
        </div>
      </div>

      {/* signal bars */}
      {triageRow && (
        <div className="grid grid-cols-7 gap-2">
          {Object.entries(triageRow.signals).map(([sig, val]) => (
            <div key={sig} className="flex flex-col gap-1">
              <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div
                  className={`h-full rounded-full ${SIGNAL_COLORS[sig] ?? "bg-slate-500"}`}
                  style={{ width: `${val}%` }}
                />
              </div>
              <div className="flex justify-between items-baseline">
                <span className="text-[10px] text-slate-500">{sig}</span>
                <span className="text-[10px] font-mono text-slate-400">{val.toFixed(0)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* attribution graph */}
      {showGraph && (
        <div className="rounded-lg border border-slate-800 overflow-hidden" style={{ height: 320 }}>
          <AttributionGraph facts={facts} clientName={triageRow?.client_name ?? clientId} />
        </div>
      )}

      {/* facts by kind */}
      {sortedKinds.map(([kind, kfacts]) => (
        <section key={kind} className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            {KIND_LABELS[kind] ?? kind}
          </h2>
          <div className="space-y-2">
            {kfacts
              .sort((a, b) => b.severity - a.severity)
              .map((f) => (
                <FactCard
                  key={f.fact_id}
                  fact={f}
                  selected={activeFact?.fact_id === f.fact_id}
                  onClick={() => onFactSelect(f)}
                />
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function FactCard({
  fact,
  selected,
  onClick,
}: {
  fact: Fact;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg border px-4 py-3 transition-colors ${
        selected
          ? "border-amber-600 bg-amber-950/30"
          : "border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:bg-slate-900"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex-shrink-0 text-xs font-mono px-1.5 py-0.5 rounded ${severityBadge(fact.severity)}`}
        >
          {fact.severity}
        </span>
        <div className="flex-1 min-w-0 space-y-1">
          <p className="text-sm font-medium leading-snug">{fact.headline}</p>
          <p className="text-xs text-slate-500 leading-relaxed">{fact.detail}</p>
        </div>
        <span
          className={`flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded font-mono ${confidenceBadge(fact.confidence)}`}
        >
          {fact.confidence}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2 ml-10">
        <span className="text-[10px] text-slate-600">
          {fact.sources.length} source{fact.sources.length !== 1 ? "s" : ""} · {fact.as_of}
        </span>
        <span className="text-[10px] text-amber-700">click to inspect →</span>
      </div>
    </button>
  );
}

// ─── fact drawer ──────────────────────────────────────────────────────────────

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
    <aside className="w-96 flex-shrink-0 border-l border-slate-800 bg-slate-950 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
        <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">
          {KIND_LABELS[fact.kind] ?? fact.kind}
        </span>
        <button
          onClick={onClose}
          className="text-slate-500 hover:text-slate-200 transition-colors text-lg leading-none"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${severityBadge(fact.severity)}`}>
              {fact.severity}
            </span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${confidenceBadge(fact.confidence)}`}>
              {fact.confidence}
            </span>
          </div>
          <h2 className="text-sm font-semibold leading-snug">{fact.headline}</h2>
          <p className="text-xs text-slate-400 leading-relaxed">{fact.detail}</p>
        </div>

        {Object.keys(fact.numbers).length > 0 && (
          <div className="space-y-1">
            <h3 className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Computed values
            </h3>
            <div className="rounded border border-slate-800 overflow-hidden">
              <table className="w-full text-xs">
                <tbody>
                  {Object.entries(fact.numbers).map(([k, v]) => (
                    <tr key={k} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-3 py-1.5 font-mono text-slate-500">{k}</td>
                      <td className="px-3 py-1.5 font-mono text-right text-slate-200">
                        {v.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="space-y-3">
          <h3 className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
            Sources ({fact.sources.length})
          </h3>
          {fact.sources.map((s) => (
            <SourceRow key={sourceKey(s)} source={s} envelope={envelope} />
          ))}
        </div>
      </div>
    </aside>
  );
}

function SourceRow({ source, envelope }: { source: Source; envelope: Envelope }) {
  const row = envelope.source_rows[sourceKey(source)];
  const cited = new Set(source.fields);

  return (
    <div className="rounded border border-slate-800 overflow-hidden">
      <div className="bg-slate-900 px-3 py-1.5 flex items-baseline gap-2">
        <span className="text-[10px] font-mono text-amber-400">{source.file}</span>
        <span className="text-[10px] text-slate-600">{source.row_ref}</span>
      </div>
      {!row ? (
        <p className="px-3 py-2 text-xs text-slate-600">Source row not found in envelope.</p>
      ) : (
        <table className="w-full text-xs">
          <tbody>
            {Object.entries(row).map(([k, v]) => (
              <tr
                key={k}
                className={`border-t border-slate-800/50 ${cited.has(k) ? "bg-amber-950/20" : ""}`}
              >
                <td className={`px-3 py-1 font-mono w-1/2 ${cited.has(k) ? "text-amber-300 font-semibold" : "text-slate-500"}`}>
                  {k}
                </td>
                <td className={`px-3 py-1 font-mono truncate ${cited.has(k) ? "text-amber-200" : "text-slate-400"}`}>
                  {v === null ? <span className="text-slate-700">null</span> : String(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── voice panel (browser TTS + /voice-query) ─────────────────────────────────

const API = "http://localhost:8000";

function VoicePanel({ clientId, clientName }: { clientId: string; clientName: string }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  const ask = useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true);
    setAnswer(null);
    try {
      const res = await fetch(`${API}/voice-query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: clientId, question: q }),
      });
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data = await res.json();
      setAnswer(data.answer);
    } catch {
      // API not running — fall back to top fact from envelope
      setAnswer(`Voice query API unavailable. Start: uvicorn api.main:app --port 8000`);
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  const speak = useCallback((text: string) => {
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 0.95;
    utt.onstart = () => setSpeaking(true);
    utt.onend = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    utteranceRef.current = utt;
    window.speechSynthesis.speak(utt);
  }, []);

  const stop = useCallback(() => {
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 rounded text-xs border border-slate-700 text-slate-400 hover:border-sky-600 hover:text-sky-400 transition-colors"
      >
        voice assist
      </button>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 w-80 rounded-xl border border-slate-700 bg-slate-900 shadow-2xl z-50 flex flex-col">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800">
        <span className="text-xs font-semibold text-sky-400">Voice Assist · {clientName}</span>
        <button onClick={() => { stop(); setOpen(false); }} className="text-slate-500 hover:text-slate-200 text-lg leading-none">×</button>
      </div>

      <div className="p-3 space-y-3">
        {/* quick prompts */}
        <div className="flex flex-wrap gap-1.5">
          {[
            "What is the top risk?",
            "Why is liquidity flagged?",
            "What is the collateral status?",
          ].map((q) => (
            <button
              key={q}
              onClick={() => { setQuestion(q); ask(q); }}
              className="text-[10px] px-2 py-1 rounded border border-slate-700 text-slate-400 hover:border-sky-700 hover:text-sky-300 transition-colors"
            >
              {q}
            </button>
          ))}
        </div>

        {/* question input */}
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(question)}
            placeholder="Ask about this client…"
            className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-600"
          />
          <button
            onClick={() => ask(question)}
            disabled={loading || !question.trim()}
            className="px-3 py-1.5 rounded bg-sky-700 hover:bg-sky-600 disabled:opacity-40 text-xs text-white transition-colors"
          >
            {loading ? "…" : "Ask"}
          </button>
        </div>

        {/* answer */}
        {answer && (
          <div className="rounded border border-slate-700 bg-slate-800/60 p-3 space-y-2">
            <p className="text-xs text-slate-200 leading-relaxed">{answer}</p>
            <div className="flex gap-2">
              {!speaking ? (
                <button
                  onClick={() => speak(answer)}
                  className="text-[10px] px-2 py-1 rounded border border-sky-800 text-sky-400 hover:bg-sky-900/30 transition-colors"
                >
                  ▶ read aloud
                </button>
              ) : (
                <button
                  onClick={stop}
                  className="text-[10px] px-2 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors animate-pulse"
                >
                  ■ stop
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── attribution graph (React Flow) ───────────────────────────────────────────

function AttributionGraph({ facts, clientName }: { facts: Fact[]; clientName: string }) {
  const { nodes, edges } = buildGraph(facts, clientName);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      colorMode="dark"
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable={false}
    >
      <Background color="#334155" gap={20} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function buildGraph(facts: Fact[], clientName: string): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // portfolio node (centre-right)
  nodes.push({
    id: "portfolio",
    position: { x: 600, y: 160 },
    data: {
      label: (
        <div className="text-xs text-center">
          <div className="font-semibold text-emerald-300">{clientName}</div>
          <div className="text-slate-400">{facts.length} facts</div>
        </div>
      ),
    },
    style: {
      background: "#052e16",
      border: "1px solid #16a34a",
      borderRadius: 8,
      padding: "8px 12px",
      color: "white",
      minWidth: 140,
    },
  });

  // one node per fact kind
  const byKind = facts.reduce<Record<string, Fact[]>>((acc, f) => {
    (acc[f.kind] ||= []).push(f);
    return acc;
  }, {});

  const kinds = Object.keys(byKind);
  kinds.forEach((kind, i) => {
    const kfacts = byKind[kind];
    const maxSev = Math.max(...kfacts.map((f) => f.severity));
    const nodeId = `kind-${kind}`;
    const y = (i - (kinds.length - 1) / 2) * 90 + 160;

    nodes.push({
      id: nodeId,
      position: { x: 280, y },
      data: {
        label: (
          <div className="text-xs">
            <div className="font-semibold text-blue-300">{KIND_LABELS[kind] ?? kind}</div>
            <div className="text-slate-400">{kfacts.length} fact{kfacts.length !== 1 ? "s" : ""} · sev {maxSev}</div>
          </div>
        ),
      },
      style: {
        background: "#0f172a",
        border: maxSev >= 80 ? "1px solid #ef4444" : maxSev >= 50 ? "1px solid #f59e0b" : "1px solid #475569",
        borderRadius: 8,
        padding: "6px 10px",
        color: "white",
        minWidth: 130,
      },
    });

    edges.push({
      id: `e-${kind}`,
      source: nodeId,
      target: "portfolio",
      animated: maxSev >= 75,
      style: { stroke: maxSev >= 75 ? "#ef4444" : "#475569" },
      label: `sev ${maxSev}`,
      labelStyle: { fill: "#94a3b8", fontSize: 10 },
    });
  });

  // source file nodes (left side)
  const sourceFiles = new Set<string>();
  facts.forEach((f) => f.sources.forEach((s) => sourceFiles.add(s.file)));

  [...sourceFiles].forEach((file, i) => {
    const nodeId = `src-${file}`;
    const y = (i - (sourceFiles.size - 1) / 2) * 70 + 160;

    nodes.push({
      id: nodeId,
      position: { x: 0, y },
      data: {
        label: (
          <div className="text-[10px] font-mono text-slate-400 text-center">{file}</div>
        ),
      },
      style: {
        background: "#0f172a",
        border: "1px solid #334155",
        borderRadius: 6,
        padding: "4px 8px",
        color: "white",
      },
    });

    // connect to all kinds that use this source
    facts.forEach((f) => {
      if (f.sources.some((s) => s.file === file)) {
        const kindId = `kind-${f.kind}`;
        const edgeId = `e-${file}-${f.kind}`;
        if (!edges.find((e) => e.id === edgeId)) {
          edges.push({
            id: edgeId,
            source: nodeId,
            target: kindId,
            style: { stroke: "#1e293b" },
          });
        }
      }
    });
  });

  return { nodes, edges };
}
