import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Live Assist. Transcript in, grounded cue cards out.
 *
 * There is no audio output anywhere in this component, and none in the service
 * behind it. The rule that the AI never speaks to a client is enforced by the
 * absence of a speaker, not by an instruction telling it to keep quiet.
 *
 * Web Speech is the primary transcript source because it needs no infra and
 * runs in the console the RM already has open. Typing is a first-class
 * fallback: a noisy demo room should not be the reason this looks broken.
 */

const ASSIST_HTTP = "http://127.0.0.1:8765";
const ASSIST_WS = "ws://127.0.0.1:8765/assist";

type Cue = {
  fact_id: string;
  kind: string;
  severity: number;
  confidence: string;
  headline: string;
  detail: string;
  numbers: Record<string, number>;
  relevance: number;
  heard: string;
  as_of: string;
  sources: {
    file: string;
    row_ref: string;
    fields: string[];
    row: Record<string, string | number | null>;
  }[];
};

type Line = { text: string; speaker: string; source: string };

// Chrome exposes this unprefixed on some builds and prefixed on others.
function speechRecognition(): any | null {
  const w = window as any;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

export default function Assist({ clientId = "CL-0002" }: { clientId?: string }) {
  const [connected, setConnected] = useState(false);
  const [listening, setListening] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [cues, setCues] = useState<Cue[]>([]);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const recogRef = useRef<any>(null);

  useEffect(() => {
    // React StrictMode mounts effects twice in dev. The first socket gets
    // closed by cleanup while still CONNECTING, which fires an error event —
    // so without this guard the console reports the service as unreachable
    // while the second socket is connected and working perfectly.
    let cancelled = false;
    const socket = new WebSocket(`${ASSIST_WS}?client_id=${clientId}`);
    socketRef.current = socket;
    socket.onopen = () => {
      if (cancelled) return;
      setConnected(true);
      setError(null); // a successful connection clears any earlier failure
    };
    socket.onclose = () => {
      if (!cancelled) setConnected(false);
    };
    socket.onerror = () => {
      if (!cancelled) setError("Assist service unreachable — run `make assist`.");
    };
    socket.onmessage = (event) => {
      if (cancelled) return;
      const message = JSON.parse(event.data);
      if (message.type === "transcript") {
        setLines((prev) => [...prev.slice(-40), message as Line]);
      } else if (message.type === "cue") {
        // Newest first: the RM reads the top of the stack mid-sentence.
        setCues((prev) => [message.cue, ...prev].slice(0, 12));
      }
    };
    return () => {
      cancelled = true;
      socket.close();
    };
  }, [clientId]);

  const send = useCallback(
    async (text: string, source: string) => {
      if (!text.trim()) return;
      try {
        await fetch(`${ASSIST_HTTP}/transcript`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: clientId, text, speaker: "rm", source }),
        });
      } catch {
        setError("Could not reach the assist service.");
      }
    },
    [clientId]
  );

  const toggleMic = useCallback(() => {
    if (listening) {
      recogRef.current?.stop();
      setListening(false);
      return;
    }
    const recognition = speechRecognition();
    if (!recognition) {
      setError("This browser has no Web Speech API. Type instead — same path.");
      return;
    }
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = "en-GB";
    recognition.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) send(result[0].transcript, "web-speech");
      }
    };
    recognition.onerror = (event: any) => setError(`Speech error: ${event.error}`);
    recognition.onend = () => setListening(false);
    recogRef.current = recognition;
    recognition.start();
    setListening(true);
  }, [listening, send]);

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Live assist · {clientId}</div>
        <h1>Listening</h1>
        <p className="lede">
          Grounded facts, surfaced as the conversation reaches them. Nothing here speaks —
          there is no audio output in this service at all, which is what makes the rule
          that the client only ever hears their RM structural rather than promised.
        </p>
      </div>

      <div className="mic">
        <button className={listening ? "" : "primary"} onClick={toggleMic}>
          {listening ? "Stop listening" : "Start listening"}
        </button>
        <span className={`dot ${listening ? "live" : ""}`} />
        <span className="hint">
          {connected ? "connected" : "not connected"} · {cues.length} cue
          {cues.length === 1 ? "" : "s"}
        </span>
      </div>

      {error && <p className="notice">{error}</p>}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(typed, "typed");
          setTyped("");
        }}
        style={{ display: "flex", gap: "var(--s2)", marginBottom: "var(--s6)", maxWidth: "620px" }}
      >
        <input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="or type what was said — same path, for a noisy room"
        />
        <button type="submit">Send</button>
      </form>

      <div className="assist">
        <div>
          {cues.length === 0 && (
            <p className="empty">
              Nothing yet. Say something about the portfolio and the relevant facts will
              appear here, newest first.
            </p>
          )}
          {cues.map((cue, index) => (
            <article className={`cue ${index === 0 ? "fresh" : ""}`} key={`${cue.fact_id}-${index}`}>
              <h4>{cue.headline}</h4>
              <p>{cue.detail}</p>
              <div className="foot">
                <span className={`chip ${cue.confidence === "verified" ? "chip-verified" : "chip-derived"}`}>
                  {cue.confidence}
                </span>
                <span>{cue.fact_id}</span>
                <span>·</span>
                <span>relevance {cue.relevance}</span>
              </div>
              <details>
                <summary>Sources ({cue.sources.length})</summary>
                {cue.sources.map((source) => (
                  <div className="source-block" key={`${source.file}-${source.row_ref}`}>
                    <div className="head">
                      <span className="file">{source.file}</span>
                      <span className="hint">{source.row_ref}</span>
                    </div>
                    <div className="kv">
                      {Object.entries(source.row).map(([key, value]) => (
                        <div key={key}>
                          <span className="k">{key}</span>
                          <span className={`v ${source.fields.includes(key) ? "cited" : ""}`}>
                            {value === null || value === "" ? "—" : String(value)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </details>
            </article>
          ))}
        </div>

        <div className="heard">
          <div className="eyebrow">Heard</div>
          <ul>
            {lines.length === 0 && <li className="hint">nothing yet</li>}
            {lines
              .slice()
              .reverse()
              .map((line, index) => (
                <li key={index}>
                  <span className="src">{line.source}</span>
                  {line.text}
                </li>
              ))}
          </ul>
        </div>
      </div>
    </>
  );
}
