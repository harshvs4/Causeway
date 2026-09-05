"""Assist: transcript in, grounded cue cards out, no audio path anywhere."""

import asyncio
from pathlib import Path

import pytest

from assist.app import (
    COOLDOWN_SECONDS,
    DograhTranscript,
    TranscriptChunk,
    _sessions,
    build_cue,
    ingest,
    load_facts,
)


@pytest.fixture(autouse=True)
def fresh():
    load_facts()
    _sessions.clear()
    yield
    _sessions.clear()


def say(text, client_id="CL-0002", **kw):
    return asyncio.run(ingest(TranscriptChunk(client_id=client_id, text=text, **kw)))


# --- the ship line ---------------------------------------------------------

def test_worrying_about_collateral_surfaces_the_facility(fresh):
    cues = say("he's worried about the collateral")
    assert cues, "nothing surfaced"
    assert all("CF-0001" in c["headline"] for c in cues)
    assert all(c["kind"] == "collateral" for c in cues)


def test_the_lombard_line_reaches_the_facility_at_all(fresh):
    """The RM says "Lombard"; no fact text contains the word - it lives on the
    cited row as facility_type. Before the index read cited rows this query
    returned nothing whatsoever. The property worth pinning is that it reaches
    the facility, not which of the four facility cards happens to lead: they
    score within 0.03 of each other and picking a winner would be tuning to a
    phrase rather than to a behaviour."""
    cues = say("he wants to draw more on the Lombard line")
    assert cues
    assert all("CF-0001" in c["headline"] for c in cues)


def test_the_trust_question_surfaces_the_trust_commitment(fresh):
    """CN-003 leads because it is the trust, and its detail carries the gap."""
    cues = say("he says he cannot fund the family trust")
    assert cues[0]["fact_id"] == "F-CL0002-LIQUIDITY-CN-003"
    assert "Not fundable" in cues[0]["detail"]


def test_asking_about_selling_surfaces_what_can_actually_be_sold(fresh):
    cues = say("how much of the portfolio can he actually sell right now")
    assert "F-CL0002-LIQUIDITY-PLEDGED-CF-0001" in {c["fact_id"] for c in cues}


def test_asking_whether_he_breached_leads_with_the_breach(fresh):
    """Stemming: the fact says 'breached', the RM says 'breach'."""
    cues = say("did he breach the facility")
    assert cues[0]["fact_id"] == "F-CL0002-COLLAT-BREACH"


def test_the_rm_notes_own_words_retrieve_the_driver_fact(fresh):
    """N-004 is cited by the driver fact, so its vocabulary is searchable."""
    assert {c["fact_id"] for c in say("I flagged this at the time")} == \
        {"F-CL0002-COLLAT-DRIVER"}


def test_triage_never_surfaces_as_a_live_cue(fresh):
    """'Ranked 1 of 20 this week' is a Monday fact, not a mid-sentence one."""
    for phrase in ["he says he cannot fund the family trust",
                   "how is he doing overall", "what should I know"]:
        _sessions.clear()
        assert all(c["kind"] != "triage" for c in say(phrase))


def test_every_cue_carries_its_sources_and_the_rows_behind_them(fresh):
    for cue in say("he's worried about the collateral"):
        assert cue["sources"]
        for source in cue["sources"]:
            assert source["fields"]
            assert source["row"], "a cue whose row is empty cannot be defended"


def test_cue_numbers_are_the_facts_own(fresh):
    from engine.grounding import explain, retrievable_sources
    import json
    from engine.loader import BUILD_DIR
    sources = retrievable_sources(json.loads((BUILD_DIR / "facts.json").read_text()),
                                  "CL-0002")
    for cue in say("he's worried about the collateral"):
        bad = [c.written for c in explain(cue["headline"], sources) if not c.grounded]
        assert bad == [], f"{cue['fact_id']} headline has ungrounded numbers: {bad}"


# --- behaviour under a real conversation -----------------------------------

def test_silence_surfaces_nothing(fresh):
    assert say("   ") == []
    assert say("mm hm, right, yes") == []


def test_a_fact_does_not_repeat_immediately(fresh):
    first = {c["fact_id"] for c in say("he's worried about the collateral")}
    second = {c["fact_id"] for c in say("still worried about that collateral")}
    assert first and not (first & second), "a prompter that repeats itself is noise"


def test_cooldown_is_long_enough_to_matter():
    assert COOLDOWN_SECONDS >= 60


def test_unknown_client_surfaces_nothing_rather_than_guessing(fresh):
    assert say("he's worried about the collateral", client_id="CL-9999") == []


def test_context_accumulates_across_turns(fresh):
    """Retrieval reads a rolling window, not a single sentence."""
    say("I want to talk about something")
    cues = say("the facility")
    assert cues


# --- the Dograh adapter ----------------------------------------------------

def test_dograh_payload_normalises_to_the_same_chunk():
    chunk = DograhTranscript(text="he's worried about the collateral",
                             role="user").to_chunk("CL-0002")
    assert chunk.client_id == "CL-0002"
    assert chunk.speaker == "rm"
    assert chunk.source == "dograh"


def test_dograh_agent_speech_is_attributed_to_the_client():
    """In rehearsal the agent plays the client, so 'assistant' is the client."""
    assert DograhTranscript(text="hi", role="assistant").to_chunk("CL-0002").speaker \
        == "client"


def test_dograh_alternate_field_name_is_accepted():
    assert DograhTranscript(transcript="hello there").to_chunk("CL-0002").text \
        == "hello there"


def test_both_sources_produce_identical_cues(fresh):
    web = say("he's worried about the collateral")
    _sessions.clear()
    chunk = DograhTranscript(text="he's worried about the collateral",
                             role="user").to_chunk("CL-0002")
    via_dograh = asyncio.run(ingest(chunk))
    assert [c["fact_id"] for c in web] == [c["fact_id"] for c in via_dograh]


# --- the governance property -----------------------------------------------

def test_a_cue_is_a_fact_never_generated_prose(fresh):
    import json
    from engine.loader import BUILD_DIR
    facts = {f["fact_id"]: f for f in
             json.loads((BUILD_DIR / "facts.json").read_text())["facts"]}
    for cue in say("he's worried about the collateral"):
        original = facts[cue["fact_id"]]
        assert cue["headline"] == original["headline"]
        assert cue["detail"] == original["detail"]
        assert cue["numbers"] == original["numbers"]


def test_the_service_exposes_no_audio_route():
    """The constraint is structural: there is no speaker to disable.

    "voice" alone is not the test - /voice-query takes a spoken question and
    returns text, which is the whole point. What must not exist is anything
    that produces sound.
    """
    from assist.app import app
    routes = " ".join(getattr(r, "path", "") for r in app.routes).lower()
    for forbidden in ("speak", "tts", "audio", "say", "synthesi"):
        assert forbidden not in routes


def test_no_speech_synthesis_dependency_is_reachable():
    """If the service could speak, some library would have to be importable."""
    import assist.app as module
    source = Path(module.__file__).read_text().lower()
    for library in ("pyttsx", "elevenlabs", "gtts", "azure.cognitiveservices.speech",
                    "openai.audio", "soundfile", "pyaudio"):
        assert library not in source


def test_a_voice_query_returns_text_and_its_sources_only():
    from assist.app import load_facts
    load_facts()
    from assist.app import voice_query, VoiceQuery
    result = asyncio.run(voice_query(VoiceQuery(client_id="CL-0002",
                                                question="is the collateral under pressure")))
    assert isinstance(result.get("answer"), (str, type(None)))
    assert "audio" not in result and "speech" not in result
    assert set(result) <= {"answer", "facts_used", "sources", "reason"}


# --- relevance shape -------------------------------------------------------

def test_a_tight_cluster_surfaces_together(fresh):
    """The four facility facts sit within 0.03 of each other; keep them together."""
    cues = say("he's worried about the collateral")
    assert len(cues) >= 3
    spread = cues[0]["relevance"] - cues[-1]["relevance"]
    assert spread < 0.1, "these are effectively tied and should not be split by rank"


def test_a_clear_winner_leads_and_the_tail_is_short(fresh):
    """When one fact plainly answers the question it leads, and the relative
    cutoff keeps the tail short rather than padding to three."""
    cues = say("he says he cannot fund the family trust")
    assert cues[0]["fact_id"] == "F-CL0002-LIQUIDITY-CN-003"
    assert len(cues) <= 2


def test_relative_cutoff_survives_a_corpus_change():
    """The bug this replaced: excluding one kind moved every score under a
    fixed floor and the ship-line phrase returned nothing at all."""
    import json
    from engine.loader import BUILD_DIR
    from engine.retrieval import FactIndex
    facts = [f for f in json.loads((BUILD_DIR / "facts.json").read_text())["facts"]
             if f["client_id"] == "CL-0002"]
    rows = json.loads((BUILD_DIR / "facts.json").read_text())["source_rows"]
    with_triage = FactIndex(facts, rows)
    without = FactIndex([f for f in facts if f["kind"] != "triage"], rows)
    query = "he's worried about the collateral"
    assert with_triage.search(query, relative_cutoff=0.6)
    assert without.search(query, relative_cutoff=0.6)


# --- a conversation is a sequence, not one question -------------------------

def test_each_question_in_a_conversation_gets_its_own_answer(fresh):
    """The reported failure: the first question worked and later ones did not.

    The window accumulated twelve utterances and every search ran against the
    whole mush, so by the third question the query was three topics at once and
    the ranking answered none of them well.
    """
    expected = [
        ("he's worried about the collateral", "F-CL0002-COLLAT-CURE"),
        ("can he actually fund the family trust", "F-CL0002-LIQUIDITY-CN-003"),
        ("what about his helios exposure", "F-CL0002-LOOKTHRU-PF-0003"),
        ("did he breach the facility", "F-CL0002-COLLAT-BREACH"),
    ]
    for phrase, wanted in expected:
        cues = say(phrase)
        assert cues, f"{phrase!r} surfaced nothing"
        assert cues[0]["fact_id"] == wanted, f"{phrase!r} led with {cues[0]['fact_id']}"


def test_the_latest_utterance_outranks_earlier_context(fresh):
    """Ask about collateral, then about Helios. The second answer must be Helios."""
    say("he's worried about the collateral")
    cues = say("what about his helios exposure")
    assert cues[0]["fact_id"] == "F-CL0002-LOOKTHRU-PF-0003"


def test_context_still_rescues_a_fragment(fresh):
    """A fragment on its own finds nothing, so the window is the fallback."""
    say("I want to talk about the facility")
    assert say("the trigger") != []
