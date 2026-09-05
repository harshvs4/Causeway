"""The grounded-fact interface for the voice layer.

Dograh agents reach Anchorline only through these five tools. That is the whole
governance argument in one sentence: an agent cannot state a portfolio figure it
did not receive from here, because it has no other route to one.

Every tool returns facts that already passed both invariants at construction -
each carries at least one source, and every number in its text was computed
rather than written. Nothing here generates prose about a portfolio.

Transport is streamable HTTP because that is what Dograh's MCP tool config
expects, and it is bound on all interfaces so the containers can reach it at
host.docker.internal.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from engine.loader import BUILD_DIR, load
from engine.retrieval import FactIndex

HOST = os.environ.get("ANCHORLINE_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("ANCHORLINE_MCP_PORT", "8848"))

# Phrases that, when present, suggest a note records the client pushing back.
# This is a keyword hint and nothing more: it can only ever under-fire, because
# a note can record resistance in words that are not on this list. A false value
# means "no listed phrase appeared", never "this client did not push back".
# The full note text is returned either way and is the authoritative record.
RESISTANCE_MARKERS = (
    "did not want", "does not want", "will not", "won't", "would not",
    "refused", "declined", "reluctant", "resisted", "pushed back",
    "uncomfortable", "agitated", "difficult call", "proceeded anyway",
    "acknowledged the point but proceeded", "not prepared to", "no interest in",
    "insisted", "adamant", "wants to avoid", "avoid selling", "prefers not",
    "no plans to", "does not intend", "holding out", "not willing",
    "views the", "wants to hold",
)


_TOKEN = re.compile(r"[a-z0-9]+")


def _log_call(tool: str, **kwargs: Any) -> None:
    """Record every invocation. Without this there is no way to tell whether an
    agent grounded an answer or merely sounded grounded."""
    detail = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
    print(f"[TOOLCALL] {tool} {detail}", flush=True)

mcp = MCPServer(
    name="anchorline",
    instructions=(
        "Grounded wealth-intelligence facts for a relationship manager's book. "
        "Every figure you state about a portfolio must come from one of these "
        "tools. If a tool does not return a number, you do not have it - say so "
        "rather than estimating."
    ),
)


@lru_cache(maxsize=1)
def _envelope() -> dict[str, Any]:
    path = BUILD_DIR / "facts.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `make build` before starting the MCP server"
        )
    return json.loads(path.read_text())


@lru_cache(maxsize=1)
def _dataset():
    return load()


def _facts_for(client_id: str) -> list[dict]:
    return [f for f in _envelope()["facts"] if f["client_id"] == client_id]


@lru_cache(maxsize=8)
def _index(client_id: str) -> FactIndex:
    return FactIndex(_facts_for(client_id), _envelope().get("source_rows", {}))


# --------------------------------------------------------------------------
# 1. list_clients
# --------------------------------------------------------------------------
@mcp.tool(
    description=(
        "List the clients Anchorline has analysed, with their position in this "
        "week's book ranking. Call this first if you do not already know which "
        "client you are working on."
    )
)
def list_clients() -> dict[str, Any]:
    _log_call("list_clients")
    envelope = _envelope()
    clients = _dataset().clients.set_index("client_id")
    ranking = {row["client_id"]: row for row in envelope["triage"]["ranking"]}

    out = []
    for client_id in envelope["clients"]:
        row = clients.loc[client_id]
        rank = ranking.get(client_id, {})
        out.append(
            {
                "client_id": client_id,
                "name": str(row["client_name"]),
                "wealth_band": str(row["wealth_band"]),
                "risk_profile": str(row["risk_profile"]),
                "triage_rank": rank.get("rank"),
                "triage_score": rank.get("score"),
                "fact_count": len(_facts_for(client_id)),
            }
        )
    return {"as_of": envelope["as_of"], "clients": out}


# --------------------------------------------------------------------------
# 2. get_client_brief
# --------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Everything known about one client: stated objectives, source of wealth, "
        "portfolios, and the highest-severity findings. Use this to prepare, and "
        "to stay consistent with what the client actually holds."
    )
)
def get_client_brief(client_id: str) -> dict[str, Any]:
    _log_call("get_client_brief", client_id=client_id)
    envelope = _envelope()
    dataset = _dataset()
    clients = dataset.clients.set_index("client_id")
    if client_id not in clients.index:
        return {"error": f"unknown client_id {client_id!r}"}

    row = clients.loc[client_id]
    portfolios = dataset.portfolios
    portfolios = portfolios[portfolios["client_id"] == client_id]
    ranking = {r["client_id"]: r for r in envelope["triage"]["ranking"]}
    facts = sorted(_facts_for(client_id), key=lambda f: -f["severity"])

    return {
        "client_id": client_id,
        "name": str(row["client_name"]),
        "as_of": envelope["as_of"],
        "wealth_band": str(row["wealth_band"]),
        "risk_profile": str(row["risk_profile"]),
        "risk_tolerance_score": int(row["risk_tolerance_score"]),
        "liquidity_needs": str(row["liquidity_needs"]),
        "life_stage": str(row["life_stage"]),
        "source_of_wealth": str(row["source_of_wealth"]),
        "stated_objectives": str(row["objectives"]),
        "triage": ranking.get(client_id, {}),
        "portfolios": [
            {
                "portfolio_id": str(p["portfolio_id"]),
                "name": str(p["portfolio_name"]),
                "service_model": str(p["service_model"]),
                "mandate": str(p["mandate_name"]),
                "aum_usd_current": float(p["aum_usd_current"]),
            }
            for _, p in portfolios.iterrows()
        ],
        "findings": [
            {
                "fact_id": f["fact_id"],
                "kind": f["kind"],
                "severity": f["severity"],
                "headline": f["headline"],
            }
            for f in facts
        ],
    }


# --------------------------------------------------------------------------
# 3. search_facts
# --------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Find the facts most relevant to a phrase, for one client. Use this when "
        "a conversation reaches a topic and you need what is actually known about "
        "it. Returns fact_ids you can pass to get_fact for the underlying rows."
    )
)
def search_facts(client_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    _log_call("search_facts", client_id=client_id, query=query)
    facts = _facts_for(client_id)
    if not facts:
        return {"client_id": client_id, "query": query, "results": []}

    return {
        "client_id": client_id,
        "query": query,
        "results": [
            {
                "fact_id": hit.fact["fact_id"],
                "kind": hit.fact["kind"],
                "severity": hit.fact["severity"],
                "relevance": round(hit.score, 3),
                "headline": hit.fact["headline"],
                "detail": hit.fact["detail"],
            }
            for hit in _index(client_id).search(query, limit)
        ],
    }


# --------------------------------------------------------------------------
# 4. get_fact
# --------------------------------------------------------------------------
@mcp.tool(
    description=(
        "One fact in full, with every number it computed and the actual dataset "
        "rows behind it. Use this before stating a figure, so what you say can be "
        "traced back to the data."
    )
)
def get_fact(fact_id: str) -> dict[str, Any]:
    _log_call("get_fact", fact_id=fact_id)
    envelope = _envelope()
    fact = next((f for f in envelope["facts"] if f["fact_id"] == fact_id), None)
    if fact is None:
        return {"error": f"unknown fact_id {fact_id!r}"}

    rows = envelope["source_rows"]
    return {
        **fact,
        "source_rows": [
            {
                "file": source["file"],
                "row_ref": source["row_ref"],
                "cited_fields": source["fields"],
                "row": rows.get(f"{source['file']}::{source['row_ref']}", {}),
            }
            for source in fact["sources"]
        ],
    }


# --------------------------------------------------------------------------
# 5. get_documented_objections
# --------------------------------------------------------------------------
@mcp.tool(
    description=(
        "What this client has actually said, verbatim, from the relationship "
        "manager's notes, plus their recorded objectives. Read every note in "
        "full: resistance_marker_detected only means a known phrase was spotted, "
        "so a note without one may still record the client pushing back. Use "
        "these to stay in character - argue the positions this client has really "
        "taken, not positions a client like them might take."
    )
)
def get_documented_objections(client_id: str) -> dict[str, Any]:
    _log_call("get_documented_objections", client_id=client_id)
    dataset = _dataset()
    clients = dataset.clients.set_index("client_id")
    if client_id not in clients.index:
        return {"error": f"unknown client_id {client_id!r}"}

    notes = dataset.rm_notes
    notes = notes[notes["client_id"] == client_id].sort_values(
        "note_date", ascending=False
    )

    documented = []
    for _, note in notes.iterrows():
        text = str(note["note"])
        lowered = text.lower()
        matched = [m for m in RESISTANCE_MARKERS if m in lowered]
        documented.append(
            {
                "note_id": str(note["note_id"]),
                "date": note["note_date"].strftime("%Y-%m-%d"),
                "channel": str(note["channel"]),
                "note": text,
                "resistance_marker_detected": bool(matched),
                "resistance_markers": matched,
            }
        )

    row = clients.loc[client_id]
    return {
        "client_id": client_id,
        "name": str(row["client_name"]),
        "stated_objectives": str(row["objectives"]),
        "source_of_wealth": str(row["source_of_wealth"]),
        "life_stage": str(row["life_stage"]),
        "notes": documented,
        "notes_with_resistance_marker": sum(
            1 for n in documented if n["resistance_marker_detected"]
        ),
        "marker_caveat": (
            "resistance_marker_detected is a keyword hint, not a classification. "
            "It can only under-fire. Read every note in full; a note with no "
            "marker may still record the client pushing back."
        ),
    }


def main() -> None:
    # Dograh runs in Docker and reaches the host as host.docker.internal, so
    # that Host header has to be allowed explicitly - the SDK rejects unknown
    # hosts by default as DNS-rebinding protection, which is the right default
    # and simply needs telling about this one caller.
    allowed = [
        f"host.docker.internal:{PORT}",
        f"localhost:{PORT}",
        f"127.0.0.1:{PORT}",
    ]
    print(f"anchorline MCP on http://{HOST}:{PORT}/mcp  (streamable-http)")
    print(f"  allowed hosts: {allowed}")
    mcp.run(
        transport="streamable-http",
        host=HOST,
        port=PORT,
        # Stateless: Dograh refreshes its tool catalogue with one-off requests
        # and does not hold a session between them.
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed),
    )


if __name__ == "__main__":
    main()
