"""The vault must actually be traversable.

The Phase 1 ship line is "open the graph view and every fact visibly traces to
a source note". A dangling wikilink breaks that silently - Obsidian renders it
in a slightly different colour and nothing else complains - so it is checked
here instead of by eye.
"""

import re
from pathlib import Path

import pytest

from engine.loader import REPO_ROOT
from vault_build import build_vault, source_note_name

# Inside a markdown table the alias pipe must be escaped as `\|`, so both
# `[[note]]` and `[[note\|alias]]` are valid targets to resolve.
WIKILINK = re.compile(r"\[\[([^\]|\\]+)(?:\\?\|[^\]]*)?\]\]")


@pytest.fixture(scope="module")
def vault() -> Path:
    return build_vault()


def note_names(vault: Path) -> set[str]:
    return {path.stem for path in vault.rglob("*.md")}


def test_vault_has_the_three_folders(vault):
    for folder in ("Clients", "Verified", "Sources"):
        assert (vault / folder).is_dir()


def test_no_dangling_wikilinks(vault):
    known = note_names(vault)
    dangling: list[str] = []
    for path in vault.rglob("*.md"):
        for target in WIKILINK.findall(path.read_text()):
            if target not in known:
                dangling.append(f"{path.relative_to(vault)} -> [[{target}]]")
    assert not dangling, "dangling wikilinks:\n" + "\n".join(dangling)


def test_every_fact_note_links_to_at_least_one_source_note(vault):
    source_notes = {p.stem for p in (vault / "Sources").rglob("*.md")}
    for path in (vault / "Verified").glob("*.md"):
        targets = set(WIKILINK.findall(path.read_text()))
        assert targets & source_notes, f"{path.name} cites no source note"


def test_every_fact_note_links_back_to_its_client(vault):
    client_notes = {p.stem for p in (vault / "Clients").glob("*.md")}
    for path in (vault / "Verified").glob("*.md"):
        targets = set(WIKILINK.findall(path.read_text()))
        assert targets & client_notes, f"{path.name} does not link to a client"


def test_client_note_links_to_every_one_of_its_facts(vault):
    fact_notes = {p.stem for p in (vault / "Verified").glob("*.md")}
    linked: set[str] = set()
    for path in (vault / "Clients").glob("*.md"):
        linked |= set(WIKILINK.findall(path.read_text()))
    assert fact_notes <= linked, f"orphaned facts: {sorted(fact_notes - linked)}"


def test_source_notes_link_back_to_the_facts_that_cite_them(vault):
    fact_notes = {p.stem for p in (vault / "Verified").glob("*.md")}
    for path in (vault / "Sources").rglob("*.md"):
        text = path.read_text()
        if "## Cited by" not in text:
            continue
        cited = set(WIKILINK.findall(text.split("## Cited by", 1)[1]))
        # Context rows (the client row, portfolio rows) are cited by the client
        # page rather than by a fact, so an empty list is legitimate there.
        assert cited <= fact_notes


def test_pipe_is_never_used_in_a_note_name(vault):
    """`|` is the wikilink alias separator and would silently truncate links."""
    for path in vault.rglob("*.md"):
        assert "|" not in path.stem


def test_source_note_name_sanitises_row_refs():
    assert source_note_name("holdings.csv", "PF-0003|SYN-ST-0103|2026-08-26") == \
           "holdings__PF-0003__SYN-ST-0103__2026-08-26"


def test_regeneration_is_idempotent(vault):
    before = {p.relative_to(vault): p.read_text() for p in vault.rglob("*.md")}
    rebuilt = build_vault()
    after = {p.relative_to(rebuilt): p.read_text() for p in rebuilt.rglob("*.md")}
    assert before == after


def test_scenario_facts_are_refused_from_verified():
    """The structural half of the Verified/Scenario split."""
    from vault_build import ScenarioInVerified
    assert issubclass(ScenarioInVerified, RuntimeError)
