"""Generated personas must argue what the client said, not a version of it.

A rehearsal is only worth having if the client pushing back is the real one. So
the thing worth testing is not that a file was produced - it is that every
position in it is traceable to a note_id, quoted exactly, and that the generator
authored no figure of its own along the way.

The strongest check here is test_a_persona_invents_no_number_of_its_own. A
persona is a prompt, and a number sitting in a prompt is a number the agent will
state out loud in a rehearsal without ever calling a tool. That is the failure
the Fact invariants exist to prevent, one layer further out.
"""

import json
import re

import pytest

from engine.grounding import claimed
from engine.loader import BUILD_DIR, REPO_ROOT, load
from mcp_server.personas.generate import (
    OUTPUT_DIR,
    TEMPLATE_NAME,
    _env,
    generate,
    notes_for,
    persona_for,
    pressure_points,
)
from mcp_server.server import RESISTANCE_MARKERS

HANDWRITTEN = REPO_ROOT / "mcp_server" / "personas" / "CL-0002_rehearse.md"
_NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _is_number(value) -> bool:
    """Finite and convertible. A NaN is neither, and int(NaN) raises."""
    try:
        return float(value) == float(value) and abs(float(value)) != float("inf")
    except (TypeError, ValueError):
        return False


def _flat(text: str) -> str:
    """One line, single-spaced. The template hard-wraps its prose, so a phrase
    worth asserting on is usually split across two lines in the output."""
    return " ".join(text.split())


@pytest.fixture(scope="module")
def ds():
    return load()


@pytest.fixture(scope="module")
def envelope():
    path = BUILD_DIR / "facts.json"
    return json.loads(path.read_text()) if path.exists() else None


@pytest.fixture(scope="module")
def personas(tmp_path_factory):
    """Rendered into a temp directory so a test run never touches the repo."""
    out = tmp_path_factory.mktemp("personas")
    generate(output_dir=out)
    return {
        path.stem.replace("_rehearse", ""): path.read_text()
        for path in out.glob("*_rehearse.md")
    }


# --- coverage ---------------------------------------------------------------

def test_every_client_in_the_book_gets_a_persona(ds, personas):
    assert set(personas) == {str(c) for c in ds.clients["client_id"]}


def test_the_handwritten_persona_is_never_overwritten(tmp_path):
    """It carries judgement a generator cannot, and is tracked in git. The
    generated article goes somewhere else and does not claim to replace it."""
    before = HANDWRITTEN.read_text()
    generate(("CL-0002",), output_dir=tmp_path)
    assert HANDWRITTEN.read_text() == before
    assert OUTPUT_DIR.name == "generated"
    assert OUTPUT_DIR != HANDWRITTEN.parent


# --- the client's own words, exactly ----------------------------------------

def test_every_note_is_quoted_verbatim(ds, personas):
    """Not "contains something similar". The exact recorded text, or the RM is
    rehearsing against a paraphrase."""
    raw = {
        str(note["note_id"]): str(note["note"])
        for _, note in ds.rm_notes.iterrows()
    }
    for client_id, text in personas.items():
        for note in notes_for(ds, client_id):
            assert raw[note["note_id"]] in text, f"{client_id}/{note['note_id']}"


def test_every_quoted_note_is_cited_by_id(ds, personas):
    """A position with no note_id beside it cannot be checked by anybody."""
    for client_id, text in personas.items():
        for note in notes_for(ds, client_id):
            assert note["note_id"] in text
            assert f"rm_notes.json::{note['note_id']}" in text


def test_the_resistance_check_is_the_one_the_mcp_tool_uses(ds):
    """Imported, not re-listed. Two copies of this list would drift, and the
    rehearsal would then disagree with the tool the RM reads."""
    import mcp_server.personas.generate as generator

    assert generator.RESISTANCE_MARKERS is RESISTANCE_MARKERS
    assert any(note["resistance_markers"] for note in notes_for(ds, "CL-0002"))


def test_a_flagged_marker_really_appears_in_the_note(ds):
    for client_id in ds.clients["client_id"]:
        for note in notes_for(ds, str(client_id)):
            for marker in note["resistance_markers"]:
                assert marker in note["note"].lower()


# --- the generator must not author a figure ---------------------------------

def test_a_persona_invents_no_number_of_its_own(ds, personas):
    """Every numeral in a persona has to come from a quoted note or from the
    client's own record. A figure in the scaffolding is one the agent can state
    in a rehearsal without ever calling a tool.

    Compared as sets of numerals rather than by excising text, because excision
    is order-dependent and wrong: stripping a risk score of "6" from the page
    also guts "2026-02-10" and leaves "202" behind looking like an invention.
    """
    clients = ds.clients.set_index("client_id")
    for client_id, text in personas.items():
        notes = notes_for(ds, client_id)
        legitimate = " ".join(
            [note["note"] for note in notes]
            + [note["date"] for note in notes]
            + [note["note_id"] for note in notes]
            + [str(value) for value in clients.loc[client_id].values]
            + [str(int(float(value))) for value in clients.loc[client_id].values
               if _is_number(value)]
            # "out of 10" is the one constant in the scaffolding, and the
            # generation date is stamped in the footer.
            + ["10"]
        )
        # engine.grounding.claimed is the codebase's own definition of "numeral
        # a reader would take as a claim" - it scrubs identifiers and ISO dates
        # and trims trailing punctuation. Using it here keeps this check
        # consistent with the one the Fact contract applies.
        allowed = {written for written, _, _ in claimed(legitimate)}
        asserted = {written for written, _, _ in claimed(text)}
        invented = asserted - allowed
        assert not invented, f"{client_id} invented: {sorted(invented)}"


def test_a_persona_binds_the_agent_to_the_tools(personas):
    """The hard rule, restated as a test: the agent is told to call a tool for
    any figure, so the prompt must not pre-load one."""
    for text in personas.values():
        assert "only if a tool returned it" in text
        assert "never invent a percentage" in text
        assert "get_client_brief" in text and "search_facts" in text


# --- pressure points are derived, not imagined ------------------------------

def test_pressure_points_name_facts_that_exist(envelope, personas):
    """Each one points the agent at a fact_id to look up rather than handing it
    a number. A dangling id would send it looking for something not there."""
    if not envelope:
        pytest.skip("no build/facts.json; run make build")
    known = {f["fact_id"] for f in envelope["facts"]}
    found_any = False
    for client_id in personas:
        for point in pressure_points(envelope, client_id):
            ids = re.findall(r"`(F-[A-Z0-9-]+)`", point)
            assert ids, point
            for fact_id in ids:
                assert fact_id in known, fact_id
                found_any = True
    assert found_any, "no client has a pressure point - the check would be vacuous"


def test_the_tightest_facility_in_the_book_produces_a_pressure_point(envelope):
    """CL-0014 sits 0.59 points from a margin call. If that does not surface as
    something he cannot answer, the rehearsal is missing his real exposure."""
    if not envelope:
        pytest.skip("no build/facts.json; run make build")
    assert any(
        "borrowing further" in point
        for point in pressure_points(envelope, "CL-0014")
    )


def test_no_pressure_points_without_findings():
    """No envelope means no derived vulnerabilities, not invented ones."""
    assert pressure_points(None, "CL-0002") == []
    assert pressure_points({"facts": []}, "CL-0002") == []


# --- behaviour the template must get right ----------------------------------

def test_a_client_with_recorded_pushback_is_told_to_push_back(personas):
    assert "Push back. Do not roll over." in personas["CL-0002"]


def test_a_client_with_no_recorded_pushback_is_not_given_any(ds, personas):
    """Inventing resistance would train the RM for a conversation she is not
    going to have."""
    for client_id, text in personas.items():
        if any(n["resistance_markers"] for n in notes_for(ds, client_id)):
            continue
        assert "Do not manufacture a fight." in text
        # and the honest caveat about the keyword check travels with it
        assert "under*-fire" in text


def test_a_client_with_no_notes_is_an_unknown_not_an_agreeable_one():
    """No client in this dataset lacks notes, so the branch is exercised
    directly. A silent persona would otherwise read as a compliant client."""
    rendered = _env().get_template(TEMPLATE_NAME).render(
        client={
            "client_id": "CL-9999", "client_name": "Test Client", "age": 50,
            "nationality": "Singapore", "rm_name": "Priscilla Ong",
            "life_stage": "Peak earning years", "source_of_wealth": "Unknown",
            "risk_profile": "Balanced", "risk_tolerance_score": 5,
            "investment_horizon_years": 10, "liquidity_needs": "Low",
            "objectives": "None recorded",
        },
        rm_first_name="Priscilla",
        notes=[],
        any_resistance=False,
        pressure_points=[],
        generated_at="2026-11-01",
    )
    assert "Nothing is on record from you" in _flat(rendered)
    assert "not an agreeable client, only an unknown one" in _flat(rendered)


def test_the_client_is_never_addressed_by_a_split_name(ds, personas):
    """Splitting a name on whitespace assumes given-name-first order. Across
    this book it does not hold - it would address Lau Chi Ming by his family
    name - so the full name is used throughout."""
    assert "you are Lau Chi Ming throughout" in personas["CL-0014"]
    clients = ds.clients.set_index("client_id")
    for client_id, text in personas.items():
        full = str(clients.loc[client_id, "client_name"])
        assert f"you are {full} throughout" in text


def test_every_persona_says_it_was_generated(personas):
    """So nobody hand-edits one and loses the edit on the next make personas."""
    for text in personas.values():
        assert "GENERATED by mcp_server/personas/generate.py" in text
        assert "make personas" in text


def test_an_unknown_client_is_an_error_not_an_empty_persona(ds):
    with pytest.raises(KeyError, match="CL-9999"):
        persona_for(ds, "CL-9999")


def test_a_missing_field_is_omitted_rather_than_defaulted(ds, personas):
    """Caught a real defect. CL-0017 is a family office and has no age: the
    column is NaN, and rendering it through an int filter produced
    "Age | 0" - a fabricated fact about a client, arriving through a Jinja
    filter rather than through an analyzer."""
    assert ds.clients.set_index("client_id").loc["CL-0017", "age"] != \
        ds.clients.set_index("client_id").loc["CL-0017", "age"]   # i.e. NaN
    text = personas["CL-0017"]
    assert "| Age |" not in text
    assert "Age | 0" not in text
    # a client who does have an age still shows it
    assert "| Age | 63 |" in personas["CL-0014"]


def test_absent_values_never_reach_the_page_as_text(personas):
    """The other half of the same failure: a NaN stringified into prose."""
    for client_id, text in personas.items():
        lowered = text.lower()
        for ghost in ("nan", "none", "nat", "null"):
            assert f"| {ghost} |" not in lowered, f"{client_id}: {ghost}"
