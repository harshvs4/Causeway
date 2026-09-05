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


# ===========================================================================
# Phase 3: the split, the index, and a graph that is actually readable
# ===========================================================================

def test_scenario_folder_is_scaffolded(vault):
    assert (vault / "Scenario").is_dir()


def test_scenario_folder_holds_no_facts_yet(vault):
    """Empty by design: the boundary exists before there is anything to put in it."""
    fact_notes = [
        p for p in (vault / "Scenario").glob("*.md")
        if "fact_id:" in p.read_text()
    ]
    assert fact_notes == []


def test_vault_readme_explains_the_split(vault):
    text = (vault / "README.md").read_text()
    assert "Verified/" in text and "Scenario/" in text
    assert "compliance reviewer" in text


def test_readme_links_to_every_client(vault):
    linked = set(WIKILINK.findall((vault / "README.md").read_text()))
    clients = {p.stem for p in (vault / "Clients").glob("*.md")}
    assert clients <= linked


def test_note_names_are_unique_across_the_vault(vault):
    """Obsidian resolves wikilinks by name, so a repeat makes links ambiguous."""
    stems = [p.stem for p in vault.rglob("*.md")]
    duplicates = {s for s in stems if stems.count(s) > 1}
    assert not duplicates, f"ambiguous note names: {duplicates}"


# --- graph legibility, checked structurally because Obsidian is not here ----

def _graph(vault):
    from collections import defaultdict
    notes = {p.stem: p.read_text() for p in vault.rglob("*.md")}
    adjacency = defaultdict(set)
    for name, text in notes.items():
        for target in WIKILINK.findall(text):
            if target in notes:
                adjacency[name].add(target)
                adjacency[target].add(name)
    return notes, adjacency


def test_the_graph_has_no_orphans(vault):
    """An isolated dot in the graph view is a note nothing can reach."""
    notes, adjacency = _graph(vault)
    assert [n for n in notes if not adjacency[n]] == []


def test_the_graph_is_one_connected_whole(vault):
    """Every note reachable from every other: the traceability chain is unbroken."""
    from collections import deque
    notes, adjacency = _graph(vault)
    start = next(iter(notes))
    seen, queue = {start}, deque([start])
    while queue:
        for neighbour in adjacency[queue.popleft()]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    assert len(seen) == len(notes)


def test_clients_are_the_hubs_and_sources_are_the_leaves(vault):
    """The shape is the argument: people at the centre, raw rows at the edge."""
    notes, adjacency = _graph(vault)
    folder = {}
    for path in vault.rglob("*.md"):
        parts = path.relative_to(vault).parts
        folder[path.stem] = parts[0] if len(parts) > 1 else "root"

    client_degrees = [len(adjacency[n]) for n in notes if folder[n] == "Clients"]
    source_degrees = [len(adjacency[n]) for n in notes if folder[n] == "Sources"]
    fact_degrees = [len(adjacency[n]) for n in notes if folder[n] == "Verified"]

    assert min(client_degrees) > max(source_degrees)
    mean = lambda xs: sum(xs) / len(xs)
    assert mean(source_degrees) < mean(fact_degrees) < mean(client_degrees)


# --- the builder owns its folders, and nothing else -------------------------

def test_obsidian_config_is_seeded_with_colour_groups(vault):
    import json
    config = json.loads((vault / ".obsidian" / "graph.json").read_text())
    queries = {group["query"] for group in config["colorGroups"]}
    assert queries == {"path:Clients", "path:Verified", "path:Scenario", "path:Sources"}


def test_rebuilding_does_not_destroy_user_settings(vault):
    """`make vault` must never wipe somebody's editor configuration."""
    from vault_build import build_vault
    sentinel = vault / ".obsidian" / "workspace.json"
    sentinel.write_text('{"mine": true}')
    build_vault()
    assert sentinel.exists() and sentinel.read_text() == '{"mine": true}'
    sentinel.unlink()


def test_seeded_graph_config_is_not_overwritten(vault):
    from vault_build import seed_obsidian_config
    assert seed_obsidian_config(vault) is False   # already present, leave it alone
