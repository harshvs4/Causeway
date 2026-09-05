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
    <section>
      <h2>Live assist — {clientId}</h2>
      <p>
        <button onClick={toggleMic}>{listening ? "Stop listening" : "Start listening"}</button>{" "}
        <small>
          socket {connected ? "connected" : "disconnected"} · {cues.length} cue
          {cues.length === 1 ? "" : "s"}
        </small>
      </p>
      {error && <p role="alert">{error}</p>}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(typed, "typed");
          setTyped("");
        }}
      >
        <input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="or type what was said"
          size={60}
        />{" "}
        <button type="submit">Send</button>
      </form>

      <h3>Cues</h3>
      {cues.length === 0 && <p>Nothing yet. Say something about the portfolio.</p>}
      {cues.map((cue, index) => (
        <article key={`${cue.fact_id}-${index}`}>
          <h4>{cue.headline}</h4>
          <p>{cue.detail}</p>
          <small>
            {cue.fact_id} · {cue.kind} · {cue.confidence} · severity {cue.severity} ·
            relevance {cue.relevance} · as of {cue.as_of}
          </small>
          <details>
            <summary>Sources ({cue.sources.length})</summary>
            {cue.sources.map((source) => (
              <div key={`${source.file}-${source.row_ref}`}>
                <code>
                  {source.file} — {source.row_ref}
                </code>
                <table>
                  <tbody>
                    {Object.entries(source.row).map(([key, value]) => (
                      <tr key={key}>
                        <td>
                          {source.fields.includes(key) ? <strong>{key}</strong> : key}
                        </td>
                        <td>{value === null ? "" : String(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </details>
          <hr />
        </article>
      ))}

      <h3>Heard</h3>
      <ul>
        {lines
          .slice()
          .reverse()
          .map((line, index) => (
            <li key={index}>
              <small>[{line.source}]</small> {line.text}
            </li>
          ))}
      </ul>
    </section>
  );
}
