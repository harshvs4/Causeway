"""Register the Anchorline MCP server with Dograh and create the Rehearse agent.

Run once, after logging into Dograh in the browser:

    export DOGRAH_TOKEN='<bearer token from the browser session>'
    .venv/bin/python scripts/dograh_setup.py

Authentication is deliberately not automated here: signing up or entering
credentials is the operator's job, not this script's. The token is read from the
environment and never written to disk.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("DOGRAH_API", "http://localhost:8000/api/v1")
TOKEN = os.environ.get("DOGRAH_TOKEN", "")
MCP_URL = os.environ.get(
    "ANCHORLINE_MCP_URL", "http://host.docker.internal:8848/mcp"
)
PERSONA = Path(__file__).resolve().parents[1] / "mcp_server" / "personas" / "CL-0002_rehearse.md"

TOOL_NAME = "anchorline"
WORKFLOW_NAME = "Anchorline Rehearse — Ravi Chandrasekaran (CL-0002)"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}\n{detail}") from exc


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DOGRAH_TOKEN is not set.\n\n"
            "  1. Open http://localhost:3010 and sign in.\n"
            "  2. In devtools, copy the bearer token the UI sends on any\n"
            "     /api/v1/ request (Network tab -> Authorization header).\n"
            "  3. export DOGRAH_TOKEN='<that token>'\n"
        )

    print(f"api      {API}")
    print(f"mcp url  {MCP_URL}")

    # --- 1. register the MCP server as a tool ------------------------------
    existing = call("GET", "/tools/")
    tools = existing if isinstance(existing, list) else existing.get("tools", [])
    match = next((t for t in tools if t.get("name") == TOOL_NAME), None)

    definition = {
        "schema_version": 1,
        "type": "mcp",
        "config": {
            "transport": "streamable_http",
            "url": MCP_URL,
            "tools_filter": [
                "list_clients",
                "get_client_brief",
                "search_facts",
                "get_fact",
                "get_documented_objections",
            ],
            "timeout_secs": 30,
        },
    }

    if match:
        tool = call("PUT", f"/tools/{match['tool_uuid']}",
                    {"name": TOOL_NAME, "description": "Grounded Anchorline facts",
                     "definition": definition})
        print(f"updated tool {match['tool_uuid']}")
        tool_uuid = match["tool_uuid"]
    else:
        tool = call("POST", "/tools/", {
            "name": TOOL_NAME,
            "description": "Grounded wealth-intelligence facts. Every portfolio "
                           "figure an agent states must come from here.",
            "category": "mcp",
            "definition": definition,
        })
        tool_uuid = tool["tool_uuid"]
        print(f"created tool {tool_uuid}")

    refreshed = call("POST", f"/tools/{tool_uuid}/mcp/refresh")
    discovered = refreshed.get("discovered_tools") or refreshed.get("tools") or []
    print(f"discovered {len(discovered)} MCP tools: "
          f"{[t.get('name') for t in discovered]}")
    if len(discovered) != 5:
        print("  WARNING: expected 5 tools. Is `make mcp` running?")

    print(f"\npersona: {PERSONA} ({PERSONA.stat().st_size} bytes)")
    print("\nNext: create the Rehearse workflow in the UI (or via the Dograh")
    print("Claude Code plugin), attach the 'anchorline' tool, and paste the")
    print("persona above as the agent prompt.")


if __name__ == "__main__":
    main()
