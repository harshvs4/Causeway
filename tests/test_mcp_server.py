"""The five tools are the voice layer's only route to a fact.

If an agent can reach a portfolio figure any other way, the governance claim
stops being structural and becomes a promise. So these test what the tools
return, and equally what they refuse to invent.
"""

import pytest

from mcp_server.server import (
    get_client_brief,
    get_documented_objections,
    get_fact,
    list_clients,
    search_facts,
)


def test_list_clients_returns_the_deep_clients_with_their_rank():
    result = list_clients()
    by_id = {c["client_id"]: c for c in result["clients"]}
    assert set(by_id) == {"CL-0001", "CL-0002"}
    assert by_id["CL-0002"]["triage_rank"] == 1
    assert by_id["CL-0002"]["fact_count"] > 0


def test_client_brief_carries_stated_intent_and_findings():
    brief = get_client_brief("CL-0002")
    assert "secondary sale" in brief["stated_objectives"]
    assert brief["life_stage"] == "Pre-liquidity event"
    assert brief["findings"], "a brief with no findings is useless in a call"
    severities = [f["severity"] for f in brief["findings"]]
    assert severities == sorted(severities, reverse=True)


def test_unknown_client_is_an_error_not_an_invention():
    assert "error" in get_client_brief("CL-9999")
    assert "error" in get_documented_objections("CL-9999")
    assert "error" in get_fact("F-NOPE")


def test_search_surfaces_collateral_facts_for_a_collateral_question():
    hits = search_facts("CL-0002", "why is my collateral under pressure", 3)
    assert hits["results"]
    assert all("COLLAT" in r["fact_id"] for r in hits["results"])


def test_search_returns_nothing_rather_than_a_bad_guess():
    hits = search_facts("CL-0002", "zebra parachute quantum", 5)
    assert hits["results"] == []


def test_get_fact_carries_the_raw_rows_behind_it():
    fact = get_fact("F-CL0002-COLLAT-BREACH")
    assert fact["numbers"]["ltv_pct"] == pytest.approx(75.6372, abs=1e-4)
    assert fact["source_rows"], "a fact without its rows cannot be defended"
    row = fact["source_rows"][0]["row"]
    assert row["facility_id"] == "CF-0001"
    assert float(row["margin_call_ltv_pct"]) == 75.0


def test_every_fact_reachable_through_the_tools_carries_a_source():
    for client_id in ("CL-0001", "CL-0002"):
        for finding in get_client_brief(client_id)["findings"]:
            assert get_fact(finding["fact_id"])["sources"]


# --- the persona's raw material --------------------------------------------

def test_objections_flag_the_refusal_to_sell_before_the_secondary():
    """N-003 is the objection the whole rehearsal turns on."""
    result = get_documented_objections("CL-0002")
    notes = {n["note_id"]: n for n in result["notes"]}
    assert notes["N-003"]["records_resistance"]
    assert "avoid selling" in " ".join(notes["N-003"]["resistance_markers"])


def test_objections_flag_the_call_where_he_was_warned_and_proceeded():
    notes = {n["note_id"]: n for n in get_documented_objections("CL-0002")["notes"]}
    assert notes["N-004"]["records_resistance"]
    assert "acknowledged the point but proceeded" in notes["N-004"]["resistance_markers"]


def test_objections_are_verbatim_not_summarised():
    """The persona must argue what he said, not a paraphrase of it."""
    from engine.loader import load
    raw = load().rm_notes
    raw = {r["note_id"]: r["note"] for _, r in raw.iterrows()}
    for note in get_documented_objections("CL-0002")["notes"]:
        assert note["note"] == raw[note["note_id"]]


def test_resistance_markers_are_shown_so_the_flag_can_be_argued_with():
    for note in get_documented_objections("CL-0002")["notes"]:
        if note["records_resistance"]:
            assert note["resistance_markers"]
            assert all(m in note["note"].lower() for m in note["resistance_markers"])


def test_a_client_with_no_recorded_pushback_is_not_given_any():
    result = get_documented_objections("CL-0001")
    for note in result["notes"]:
        if not note["records_resistance"]:
            assert note["resistance_markers"] == []


# --- the persona itself -----------------------------------------------------

def test_persona_exists_and_binds_the_agent_to_the_tools():
    from pathlib import Path
    persona = (
        Path(__file__).resolve().parents[1]
        / "mcp_server" / "personas" / "CL-0002_rehearse.md"
    ).read_text()
    assert "get_client_brief" in persona and "search_facts" in persona
    assert "only if a tool returned it" in persona
    assert "never invent a percentage" in persona
