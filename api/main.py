"""Anchorline FastAPI backend.

Endpoints:

  GET  /triage                 — ranked call queue, all 20 clients
  GET  /client/{id}            — full brief: facts, signals, rm_notes, graph
  POST /voice-query            — grounded BM25 answer (no LLM), safe for Dograh
  GET  /attribution/{id}       — event×holding delta rows for one client
  WS   /assist/{id}            — live in-call cue cards via WebSocket
  GET  /signals/{instrument_id}— price momentum signals
  GET  /health

Run:
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine import loader, signals as signals_mod, attribution as attribution_mod, scenario as scenario_mod

# ── startup: load everything once ────────────────────────────────────────────

_BUILD = Path(__file__).resolve().parents[1] / "build" / "facts.json"

app = FastAPI(title="Anchorline", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_envelope() -> dict[str, Any]:
    if not _BUILD.exists():
        raise RuntimeError("build/facts.json not found — run `make build` first.")
    return json.loads(_BUILD.read_text())


def _load_signals() -> dict[str, Any]:
    ds = loader.load()
    return signals_mod.compute_all(ds.instruments)


# Load at import time (uvicorn worker start). Small enough to be instant.
_envelope: dict[str, Any] = _load_envelope()
_signals: dict[str, Any] = _load_signals()
_ds = loader.load()
_attribution: list[dict] = [r.as_dict() for r in attribution_mod.compute(_ds)]
_rm_notes_by_client: dict[str, list[dict]] = {}

# Index rm_notes per client — _ds already loaded above
for _, row in _ds.rm_notes.iterrows():
    cid = str(row.get("client_id", ""))
    if not cid:
        continue
    _rm_notes_by_client.setdefault(cid, []).append(
        {k: (v if not hasattr(v, "isoformat") else v.isoformat()) for k, v in row.items()}
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _facts_for(client_id: str) -> list[dict]:
    return [f for f in _envelope["facts"] if f["client_id"] == client_id]


def _signals_for_client(client_id: str) -> dict[str, Any]:
    """Return signal dict keyed by instrument_id for holdings owned by this client."""
    holdings = _ds.holdings[_ds.holdings["client_id"] == client_id]
    instrument_ids = holdings["instrument_id"].dropna().unique().tolist()
    return {iid: _signals[iid] for iid in instrument_ids if iid in _signals}


def _build_graph(facts: list[dict], client_name: str) -> dict[str, list]:
    """Build React Flow nodes and edges for a client's attribution graph."""
    nodes: list[dict] = []
    edges: list[dict] = []

    # Portfolio node
    nodes.append({
        "id": "portfolio",
        "type": "default",
        "position": {"x": 600, "y": 200},
        "data": {"label": f"{client_name}\n{len(facts)} facts"},
    })

    # Group facts by kind
    by_kind: dict[str, list[dict]] = {}
    for f in facts:
        by_kind.setdefault(f["kind"], []).append(f)

    for i, (kind, kfacts) in enumerate(by_kind.items()):
        max_sev = max(f["severity"] for f in kfacts)
        node_id = f"kind-{kind}"
        y = (i - (len(by_kind) - 1) / 2) * 90 + 200
        nodes.append({
            "id": node_id,
            "type": "default",
            "position": {"x": 300, "y": y},
            "data": {"label": f"{kind}\n{len(kfacts)} facts · sev {max_sev}"},
        })
        edges.append({
            "id": f"e-{kind}",
            "source": node_id,
            "target": "portfolio",
            "animated": max_sev >= 75,
            "label": f"sev {max_sev}",
        })

    # Source file nodes
    source_files: set[str] = set()
    for f in facts:
        for s in f.get("sources", []):
            source_files.add(s["file"])

    for j, sf in enumerate(sorted(source_files)):
        node_id = f"src-{sf}"
        y = (j - (len(source_files) - 1) / 2) * 70 + 200
        nodes.append({
            "id": node_id,
            "type": "default",
            "position": {"x": 0, "y": y},
            "data": {"label": sf},
        })
        # Connect to all kinds that cite this file
        for kind, kfacts in by_kind.items():
            if any(s["file"] == sf for f in kfacts for s in f.get("sources", [])):
                edge_id = f"e-{sf}-{kind}"
                if not any(e["id"] == edge_id for e in edges):
                    edges.append({"id": edge_id, "source": node_id, "target": f"kind-{kind}"})

    return {"nodes": nodes, "edges": edges}


def _bm25_search(facts: list[dict], query: str, top_k: int = 3) -> list[dict]:
    """Minimal keyword search over fact headline + detail. No external deps."""
    tokens = set(query.lower().split())
    scored = []
    for f in facts:
        text = f"{f['headline']} {f['detail']}".lower()
        score = sum(1 for t in tokens if t in text)
        if score > 0:
            scored.append((score, f))
    scored.sort(key=lambda x: (-x[0], -x[1]["severity"]))
    return [f for _, f in scored[:top_k]]


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/triage")
def get_triage() -> list[dict]:
    """All clients ranked by severity score. Call rank 1 first."""
    ranking = _envelope["triage"]["ranking"]
    facts = _envelope["facts"]

    # Attach top recommendation per client
    result = []
    for row in ranking:
        cid = row["client_id"]
        client_facts = [f for f in facts if f["client_id"] == cid]
        top_fact = max(client_facts, key=lambda f: f["severity"]) if client_facts else None
        result.append({
            **row,
            "top_rule": top_fact["kind"] if top_fact else None,
            "top_recommendation": top_fact["headline"] if top_fact else None,
        })
    return result


@app.get("/client/{client_id}")
def get_client(client_id: str) -> dict:
    """Full client brief: facts, signals, rm_notes, graph."""
    triage = _envelope["triage"]["ranking"]
    triage_row = next((r for r in triage if r["client_id"] == client_id), None)
    if not triage_row:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    facts = _facts_for(client_id)
    client_signals = _signals_for_client(client_id)
    rm_notes = _rm_notes_by_client.get(client_id, [])
    graph = _build_graph(facts, triage_row["client_name"])

    return {
        "client_id": client_id,
        "client_name": triage_row["client_name"],
        "triage": triage_row,
        "facts": facts,
        "signals": client_signals,
        "rm_notes": rm_notes,
        "graph_nodes": graph["nodes"],
        "graph_edges": graph["edges"],
        "source_rows": {
            k: v for k, v in _envelope["source_rows"].items()
            if any(
                f"{s['file']}::{s['row_ref']}" == k
                for f in facts
                for s in f.get("sources", [])
            )
        },
    }


class VoiceQueryRequest(BaseModel):
    client_id: str
    question: str


@app.post("/voice-query")
def voice_query(req: VoiceQueryRequest) -> dict:
    """Grounded answer for a voice question about a client.

    Search is keyword-based over fact text. Answer is always a template
    sentence — no LLM generation, no invented numbers. Safe for Dograh.
    """
    triage = _envelope["triage"]["ranking"]
    triage_row = next((r for r in triage if r["client_id"] == req.client_id), None)
    if not triage_row:
        raise HTTPException(status_code=404, detail=f"Client {req.client_id} not found")

    facts = _facts_for(req.client_id)
    if not facts:
        return {
            "answer": f"No grounded facts are available for {triage_row['client_name']} yet.",
            "facts_used": [],
        }

    matched = _bm25_search(facts, req.question, top_k=3)
    if not matched:
        # Fall back to highest-severity fact
        matched = [max(facts, key=lambda f: f["severity"])]

    name = triage_row["client_name"]
    score = triage_row["score"]
    top = matched[0]

    if len(matched) == 1:
        answer = (
            f"{name} (severity score {score:.0f}) is flagged for {top['kind']}. "
            f"{top['headline']} "
            f"{top['detail']}"
        )
    else:
        headlines = " Additionally, ".join(f['headline'] for f in matched[1:])
        answer = (
            f"{name} (severity score {score:.0f}) is flagged for {top['kind']}. "
            f"{top['headline']} "
            f"Additionally: {headlines}"
        )

    return {
        "answer": answer,
        "facts_used": [f["fact_id"] for f in matched],
    }


@app.get("/attribution/{client_id}")
def get_attribution(client_id: str) -> list[dict]:
    """Event×holding attribution rows for one client, sorted by abs delta."""
    rows = [r for r in _attribution if r["client_id"] == client_id]
    if not rows:
        raise HTTPException(status_code=404, detail=f"No attribution for {client_id}")
    return sorted(rows, key=lambda r: abs(r["value_delta_pct"]), reverse=True)


@app.websocket("/assist/{client_id}")
async def assist_ws(websocket: WebSocket, client_id: str) -> None:
    """Live in-call cue cards via WebSocket.

    The RM's transcript is sent as text frames. The server runs BM25 over
    fact text and returns the top matching fact as a JSON cue card.
    Only the RM sees this — the client never hears it.

    Message format in:  {"transcript": "the client just said..."}
    Message format out: {"cue": "...", "fact_id": "...", "severity": 90,
                         "kind": "tension", "confidence": 0.8}
    """
    triage = _envelope["triage"]["ranking"]
    triage_row = next((r for r in triage if r["client_id"] == client_id), None)
    if not triage_row:
        await websocket.close(code=1008, reason=f"Client {client_id} not found")
        return

    await websocket.accept()
    facts = _facts_for(client_id)

    try:
        while True:
            data = await websocket.receive_json()
            transcript = str(data.get("transcript", ""))
            if not transcript.strip():
                continue

            matched = _bm25_search(facts, transcript, top_k=1)
            if not matched:
                await websocket.send_json({"cue": None})
                continue

            top = matched[0]
            # Also check attribution for relevant events
            attr_rows = [r for r in _attribution if r["client_id"] == client_id]
            relevant_event = None
            for ar in attr_rows:
                desc = str(ar.get("matched_event_description") or "").lower()
                if any(t in desc for t in transcript.lower().split()):
                    relevant_event = ar
                    break

            cue = top["headline"]
            if relevant_event and relevant_event.get("matched_event_description"):
                cue += (
                    f" Context: {relevant_event['matched_event_description'][:120]}"
                    f" ({relevant_event['snapshot_from']}→{relevant_event['snapshot_to']},"
                    f" {relevant_event['value_delta_pct']:+.1f}%)"
                )

            await websocket.send_json({
                "cue": cue,
                "detail": top["detail"],
                "fact_id": top["fact_id"],
                "severity": top["severity"],
                "kind": top["kind"],
                "confidence": top["confidence"],
            })
    except WebSocketDisconnect:
        pass


@app.get("/signals/{instrument_id}")
def get_signals(instrument_id: str) -> dict:
    """Price momentum signals for a single instrument (from instruments.csv)."""
    sig = _signals.get(instrument_id)
    if not sig:
        raise HTTPException(status_code=404, detail=f"No signals for {instrument_id}")
    return sig


@app.get("/scenario/{scenario_name}")
def get_scenario(scenario_name: str) -> list[dict]:
    """Shock propagation for all clients under a named scenario.

    Valid names: hormuz_escalate | hormuz_reopen
    Sorted worst-impact first.
    """
    valid = ("hormuz_escalate", "hormuz_reopen")
    if scenario_name not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown scenario. Valid: {valid}")
    results = scenario_mod.compute(_ds, scenario_name)  # type: ignore[arg-type]
    return [r.as_dict() for r in results]


@app.get("/scenario/{scenario_name}/client/{client_id}")
def get_scenario_client(scenario_name: str, client_id: str) -> dict:
    """Scenario impact for a single client."""
    valid = ("hormuz_escalate", "hormuz_reopen")
    if scenario_name not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown scenario. Valid: {valid}")
    results = scenario_mod.compute(_ds, scenario_name)  # type: ignore[arg-type]
    row = next((r for r in results if r.client_id == client_id), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
    return row.as_dict()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "facts": _envelope["fact_count"],
        "clients": len(_envelope["clients"]),
        "signals": len(_signals),
        "attribution_rows": len(_attribution),
        "scenarios": ["hormuz_escalate", "hormuz_reopen"],
        "as_of": _envelope["as_of"],
    }
