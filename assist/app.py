"""Assist — the RM's live prompter.

Transcript chunks arrive, grounded cue cards go out. There is deliberately no
audio output path anywhere in this service: the constraint that the AI never
speaks to a client is enforced by the absence of a speaker, not by a prompt
telling it to stay quiet. That is a much stronger claim, and it is why Assist
is built on a transcript stream rather than on a voice agent.

Two transcript sources feed the same endpoint:
  - the browser's Web Speech API, which the console already has open
  - Dograh, via an adapter that normalises its payload

Everything downstream of the transcript is identical, so the demo survives the
Docker stack having a bad day.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine.loader import BUILD_DIR
from engine.retrieval import FactIndex

# A cue must clear this to be worth interrupting a conversation for. Kept low
# and absolute only to reject noise; the real filter is RELATIVE_CUTOFF.
MIN_RELEVANCE = 0.15
# Keep facts scoring within this fraction of the best hit. Adapts to the
# query instead of assuming BM25 scores mean the same thing every time.
RELATIVE_CUTOFF = 0.6
# How long before the same fact may surface again, in seconds. Live calls circle
# back; a prompter that repeats itself every sentence is noise.
COOLDOWN_SECONDS = 90.0
# Rolling window of recent speech used for retrieval. One sentence is too little
# context, the whole call is too much.
WINDOW_CHARS = 400

# Kinds that are never a live cue. "You are ranked 1 of 20 this week" is useful
# on a Monday morning and useless mid-sentence, and its boilerplate detail is
# identical across clients, so it matches loosely on words like "trust" and
# "score" and crowds out facts that answer the actual question.
NON_CONVERSATIONAL_KINDS = frozenset({"triage"})


class TranscriptChunk(BaseModel):
    client_id: str
    text: str
    speaker: str = "rm"            # rm | client | unknown
    source: str = "web-speech"     # web-speech | dograh | typed
    final: bool = True


@dataclass
class Session:
    client_id: str
    window: deque[str] = field(default_factory=lambda: deque(maxlen=12))
    last_surfaced: dict[str, float] = field(default_factory=dict)
    sockets: set[WebSocket] = field(default_factory=set)

    def recent(self) -> str:
        joined = " ".join(self.window)
        return joined[-WINDOW_CHARS:]

    def may_surface(self, fact_id: str, now: float) -> bool:
        previous = self.last_surfaced.get(fact_id)
        return previous is None or (now - previous) > COOLDOWN_SECONDS


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    load_facts()
    yield


app = FastAPI(title="Anchorline Assist", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sessions: dict[str, Session] = defaultdict(lambda: Session(client_id="?"))
_envelope: dict[str, Any] = {}
_indexes: dict[str, FactIndex] = {}
_facts_by_id: dict[str, dict] = {}


def load_facts() -> None:
    global _envelope, _indexes, _facts_by_id
    path = BUILD_DIR / "facts.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `make build` first")
    _envelope = json.loads(path.read_text())
    grouped: dict[str, list[dict]] = defaultdict(list)
    for fact in _envelope["facts"]:
        if fact["kind"] in NON_CONVERSATIONAL_KINDS:
            continue
        grouped[fact["client_id"]].append(fact)
    rows = _envelope.get("source_rows", {})
    _indexes = {cid: FactIndex(items, rows) for cid, items in grouped.items()}
    _facts_by_id = {f["fact_id"]: f for f in _envelope["facts"]}


def session_for(client_id: str) -> Session:
    if client_id not in _sessions or _sessions[client_id].client_id != client_id:
        _sessions[client_id] = Session(client_id=client_id)
    return _sessions[client_id]


def build_cue(fact: dict, relevance: float, heard: str) -> dict[str, Any]:
    """A cue card. Never prose about a portfolio — only a fact, and its sources."""
    rows = _envelope.get("source_rows", {})
    return {
        "fact_id": fact["fact_id"],
        "kind": fact["kind"],
        "severity": fact["severity"],
        "confidence": fact["confidence"],
        "headline": fact["headline"],
        "detail": fact["detail"],
        "numbers": fact["numbers"],
        "as_of": fact["as_of"],
        "relevance": round(relevance, 3),
        "heard": heard,
        "sources": [
            {
                "file": s["file"],
                "row_ref": s["row_ref"],
                "fields": s["fields"],
                "row": rows.get(f"{s['file']}::{s['row_ref']}", {}),
            }
            for s in fact["sources"]
        ],
    }


async def broadcast(session: Session, message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for socket in list(session.sockets):
        try:
            await socket.send_json(message)
        except Exception:
            dead.append(socket)
    for socket in dead:
        session.sockets.discard(socket)


async def ingest(chunk: TranscriptChunk) -> list[dict[str, Any]]:
    """Take a piece of speech, decide what the RM needs on screen."""
    session = session_for(chunk.client_id)
    text = chunk.text.strip()
    if not text:
        return []
    session.window.append(text)

    index = _indexes.get(chunk.client_id)
    if index is None:
        return []

    now = time.monotonic()
    cues: list[dict[str, Any]] = []
    for hit in index.search(session.recent(), limit=3,
                            min_score=MIN_RELEVANCE,
                            relative_cutoff=RELATIVE_CUTOFF):
        if not session.may_surface(hit.fact_id, now):
            continue
        session.last_surfaced[hit.fact_id] = now
        cues.append(build_cue(hit.fact, hit.score, text))

    await broadcast(
        session,
        {"type": "transcript", "text": text, "speaker": chunk.speaker,
         "source": chunk.source},
    )
    for cue in cues:
        await broadcast(session, {"type": "cue", "cue": cue})
    return cues


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "clients": sorted(_indexes),
        "facts": len(_facts_by_id),
        "as_of": _envelope.get("as_of"),
    }


@app.post("/transcript")
async def post_transcript(chunk: TranscriptChunk) -> dict[str, Any]:
    """Primary path: the console's Web Speech capture posts here."""
    cues = await ingest(chunk)
    return {"accepted": True, "cues": cues}


class DograhTranscript(BaseModel):
    """Adapter for Dograh's transcript payload.

    Kept deliberately thin. Everything downstream is the same code path as the
    browser's, so whichever source is available on the day, the RM sees the
    same cues.
    """

    client_id: str | None = None
    workflow_run_id: int | None = None
    text: str | None = None
    transcript: str | None = None
    role: str | None = None
    speaker: str | None = None
    is_final: bool = True

    def to_chunk(self, fallback_client: str) -> TranscriptChunk:
        speaker = (self.role or self.speaker or "unknown").lower()
        if speaker in ("assistant", "agent", "bot"):
            speaker = "client"     # in rehearsal the agent plays the client
        elif speaker in ("user", "human", "caller"):
            speaker = "rm"
        return TranscriptChunk(
            client_id=self.client_id or fallback_client,
            text=(self.text or self.transcript or ""),
            speaker=speaker,
            source="dograh",
            final=self.is_final,
        )


@app.post("/dograh/transcript")
async def post_dograh(payload: DograhTranscript,
                      client_id: str = "CL-0002") -> dict[str, Any]:
    chunk = payload.to_chunk(client_id)
    if not chunk.text.strip():
        return {"accepted": False, "reason": "empty transcript", "cues": []}
    cues = await ingest(chunk)
    return {"accepted": True, "cues": cues}


@app.websocket("/assist")
async def assist(websocket: WebSocket, client_id: str = "CL-0002") -> None:
    """Cue cards out. There is no audio channel here, by design."""
    await websocket.accept()
    session = session_for(client_id)
    session.sockets.add(websocket)
    await websocket.send_json(
        {"type": "ready", "client_id": client_id,
         "facts": len(_indexes.get(client_id).facts) if client_id in _indexes else 0}
    )
    try:
        while True:
            # Typed transcript is a legitimate fallback: a noisy demo room
            # should not be the reason the system looks broken.
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"text": raw}
            if payload.get("text"):
                await ingest(
                    TranscriptChunk(
                        client_id=client_id,
                        text=payload["text"],
                        speaker=payload.get("speaker", "rm"),
                        source=payload.get("source", "typed"),
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        session.sockets.discard(websocket)


def main() -> None:
    import uvicorn

    load_facts()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
