"""Tests for the eight selfmod-bridge spell handlers.

Every handler is an async bound method on the rune (spec §5.2). With
state ``None`` every handler fails loudly with ``state_not_initialized``
(spec §5.3) — tested once per handler below. Mutating results carry
``effective_after: "reload"`` plus the human staleness note.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from mvge.agent.function_spell import discover_spells_from_dir
from selfmod_bridge_conftest import FakeApi, make_rune, read_skill_frontmatter

SPELL_NAMES = [
    "scaffold_spell",
    "scaffold_rune",
    "scaffold_skill",
    "extension_status",
    "revise_persona",
    "teach",
    "self_snapshot",
    "self_rollback",
]

# -- state-none loud failure for every handler -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("spell", SPELL_NAMES)
async def test_state_none_fails_loudly(tmp_path: Path, spell: str) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    result = await getattr(rune, spell)({})
    assert result["ok"] is False
    assert result["error"] == "state_not_initialized"
    assert "message" in result
    if spell in {"extension_status", "self_snapshot"}:
        # Read-only spells stay exempt from the reload-staleness fields.
        assert "effective_after" not in result
        assert "note" not in result
    else:
        # Mutating spells carry them: a reload is the recovery path.
        assert result["effective_after"] == "reload"
        assert isinstance(result["note"], str) and result["note"]


# -- scaffold_spell --------------------------------------------------------


@pytest.mark.asyncio
async def test_scaffold_spell_creates_file_and_seeds_agents_md(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None
    result = await rune.scaffold_spell({"name": "hello_world", "description": "Says hello."})
    assert result["ok"] is True
    target = state.spells_dir / "hello_world.py"  # type: ignore[union-attr]
    assert Path(result["path"]) == target
    assert target.is_file()
    # The injected section tells the model to read the spells-dir
    # AGENTS.md; the instruction must not dangle — it is seeded.
    agents_md = state.spells_dir / "AGENTS.md"  # type: ignore[union-attr]
    assert agents_md.is_file()
    assert result["effective_after"] == "reload"
    assert "note" in result


@pytest.mark.asyncio
async def test_scaffold_spell_discovers_through_real_engine(tmp_path: Path) -> None:
    """The scaffolded spell loads via the real engine discovery path —
    stem-named callable wins, exactly one public function."""

    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_spell({"name": "ping_test", "description": "Pings."})
    assert result["ok"] is True
    state = rune.state
    assert state is not None
    spells = discover_spells_from_dir(state.spells_dir)  # type: ignore[arg-type]
    found = [s for s in spells if getattr(s, "name", None) == "ping_test"]
    assert len(found) == 1
    # Discovery finds exactly the stem-named spell through the real engine
    # path, and the stub body is honest: NotImplementedError until the
    # model implements it.
    with pytest.raises(NotImplementedError, match="Implement ping_test"):
        await found[0].execute("test-cast-id", {"text": "hello"})


@pytest.mark.asyncio
async def test_scaffold_spell_never_overwrites(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert (await rune.scaffold_spell({"name": "taken", "description": "x"}))["ok"] is True
    result = await rune.scaffold_spell({"name": "taken", "description": "y"})
    assert result["ok"] is False
    assert result["error"] == "exists"


@pytest.mark.asyncio
async def test_scaffold_spell_no_spells_dir_fails_loudly(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.spells_dir = None
    result = await rune.scaffold_spell({"name": "x", "description": "y"})
    assert result["ok"] is False
    assert result["error"] == "no_spells_dir"


@pytest.mark.asyncio
async def test_scaffold_spell_invalid_name(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_spell({"name": "Not-Lower", "description": "y"})
    assert result["ok"] is False
    assert result["error"] == "invalid_name"


# -- scaffold_rune ---------------------------------------------------------


@pytest.mark.asyncio
async def test_scaffold_rune_full_tree_and_manifest_parse(tmp_path: Path) -> None:

    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_rune({"name": "demo_rune", "description": "A demo rune."})
    assert result["ok"] is True
    root = Path(result["path"])
    expected = {
        "rune.py",
        "cli.py",
        "manifest.json",
        "pyproject.toml",
        "README.md",
        "mvgeos_runes_demo_rune/__init__.py",
        "mvgeos_runes_demo_rune/rune.py",
        "tests/test_skeleton.py",
    }
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".selfmod-staging-" not in p.as_posix()
    }
    assert expected == actual
    # No staging residue.
    assert not list(root.parent.glob(".selfmod-staging-*"))

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "demo_rune"
    assert manifest["description"] == "A demo rune."
    assert manifest["entry_point"] == "rune.py"
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "demo_rune"
    assert result["effective_after"] == "reload"


@pytest.mark.asyncio
async def test_scaffold_rune_materialized_import(tmp_path: Path, monkeypatch: Any) -> None:
    """The scaffolded tree is a real loadable rune, not just text."""

    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_rune({"name": "load_me", "description": "x"})
    assert result["ok"] is True
    root = Path(result["path"])
    monkeypatch.syspath_prepend(str(root))
    # Unique module name: the selfmod-bridge root rune.py is already
    # imported as "rune" by the test session.

    spec = importlib.util.spec_from_file_location("scaffolded_rune_load_me", root / "rune.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The engine loads the entry module and calls rune_factory (loader.py).
    assert callable(module.rune_factory)
    instance = module.rune_factory(FakeApi())
    assert isinstance(instance, module.LoadMeRune)


@pytest.mark.asyncio
async def test_scaffold_rune_never_overwrites_empty_dir(tmp_path: Path) -> None:
    """POSIX os.replace would atomically replace an existing EMPTY dir —
    the explicit verify under the lock is what stops it (§6.2)."""
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None
    target = state.runes_paths[0] / "keep_me"
    target.mkdir()
    result = await rune.scaffold_rune({"name": "keep_me", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "exists"
    assert target.is_dir()
    assert list(target.iterdir()) == []


@pytest.mark.asyncio
async def test_scaffold_rune_concurrent_same_name_one_winner(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    params = {"name": "race_rune", "description": "x"}
    results = await asyncio.gather(rune.scaffold_rune(params), rune.scaffold_rune(params))
    oks = [r for r in results if r["ok"]]
    exists = [r for r in results if r.get("error") == "exists"]
    assert len(oks) == 1
    assert len(exists) == 1


@pytest.mark.asyncio
async def test_scaffold_rune_relative_target_dir_is_outside(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_rune(
        {"name": "x", "description": "y", "target_dir": "relative/path"}
    )
    assert result["ok"] is False
    assert result["error"] == "outside_runes_paths"


@pytest.mark.asyncio
async def test_scaffold_rune_outside_configured_root(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    result = await rune.scaffold_rune({"name": "x", "description": "y", "target_dir": str(other)})
    assert result["ok"] is False
    assert result["error"] == "outside_runes_paths"


@pytest.mark.asyncio
async def test_scaffold_rune_explicit_target_dir(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None
    second = tmp_path / "runes2"
    second.mkdir()
    state.runes_paths.append(second)
    result = await rune.scaffold_rune(
        {"name": "placed", "description": "x", "target_dir": str(second)}
    )
    assert result["ok"] is True
    assert Path(result["path"]).parent == second


@pytest.mark.asyncio
async def test_scaffold_rune_no_runes_paths(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.runes_paths = []
    result = await rune.scaffold_rune({"name": "x", "description": "y"})
    assert result["ok"] is False
    assert result["error"] == "no_runes_paths"


# -- scaffold_skill --------------------------------------------------------


@pytest.mark.asyncio
async def test_scaffold_skill_agent_scope_and_frontmatter_roundtrip(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The scaffolded SKILL.md satisfies the published frontmatter schema.

    Asserted against the schema in ``selfmod_bridge_conftest`` rather than by
    calling the skills-bridge parser: a sibling Rune's package is not on this
    Rune's import path, and it is not this Rune's job to prove that one loader
    in particular reads the file.
    """

    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None
    (state.config_dir / "skills").mkdir()  # type: ignore[union-attr]
    description = 'Teaches "quotes", `backticks`, ${braces},\nnewlines, and \U0001f680 non-BMP.'
    result = await rune.scaffold_skill(
        {"name": "demoskill", "description": description, "scope": "agent"}
    )
    assert result["ok"] is True
    skill_md = Path(result["path"]) / "SKILL.md"
    assert skill_md.is_file()
    parsed = read_skill_frontmatter(Path(result["path"]))
    assert parsed.name == "demoskill"
    assert parsed.description == description.strip()
    assert result["effective_after"] == "reload"


@pytest.mark.asyncio
async def test_scaffold_skill_project_scope_uses_cwd(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    (tmp_path / "skills").mkdir()
    result = await rune.scaffold_skill(
        {"name": "projskill", "description": "x", "scope": "project"}
    )
    assert result["ok"] is True
    assert Path(result["path"]).parent == tmp_path / "skills"


@pytest.mark.asyncio
async def test_scaffold_skill_user_scope_honors_global_dir(
    tmp_path: Path, monkeypatch: Any
) -> None:
    global_dir = tmp_path / "global"
    (global_dir / "skills").mkdir(parents=True)
    monkeypatch.setenv("MVGEOS_GLOBAL_DIR", str(global_dir))
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_skill({"name": "userskill", "description": "x", "scope": "user"})
    assert result["ok"] is True
    assert Path(result["path"]).parent == global_dir / "skills"


@pytest.mark.asyncio
async def test_scaffold_skill_missing_scope_dir_fails_loudly(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_skill({"name": "x", "description": "y", "scope": "agent"})
    assert result["ok"] is False
    assert result["error"] == "no_skills_dir"


@pytest.mark.asyncio
async def test_scaffold_skill_shadow_warning(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None
    assert state.config_dir is not None
    (state.config_dir / "skills").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "dupskill").mkdir()
    result = await rune.scaffold_skill({"name": "dupskill", "description": "x", "scope": "agent"})
    assert result["ok"] is True
    assert any("shadow" in w for w in result.get("warnings", []))


@pytest.mark.asyncio
async def test_scaffold_skill_empty_description_rejected(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_skill({"name": "x", "description": "  "})
    assert result["ok"] is False
    assert result["error"] == "missing_description"


@pytest.mark.asyncio
async def test_scaffold_skill_invalid_scope(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_skill({"name": "x", "description": "y", "scope": "galaxy"})
    assert result["ok"] is False
    assert result["error"] == "invalid_scope"


# -- extension_status ------------------------------------------------------


@pytest.mark.asyncio
async def test_extension_status_is_truthful(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.extension_status({})
    assert result["ok"] is True
    assert result["agent_name"] == "test-agent"
    assert result["spells_dir_agents_md"] is False
    assert len(result["runes_paths"]) == 1
    assert result["runes_paths"][0]["agents_md"] is False
    assert result["system_path_exists"] is True
    assert result["snapshots"] == []


@pytest.mark.asyncio
async def test_extension_status_warns_on_unresolved(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.runes_paths.append(tmp_path / "missing")
    rune.state.spells_dir = None
    result = await rune.extension_status({})
    assert any("does not exist" in w for w in result["warnings"])
    assert any("no active spells dir" in w for w in result["warnings"])


# -- revise_persona --------------------------------------------------------


@pytest.mark.asyncio
async def test_revise_persona_replaces_exactly_one(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    result = await rune.revise_persona(
        {"old_text": "You are a test agent.", "new_text": "You are a bold agent."}
    )
    assert result["ok"] is True
    assert "bold agent" in state.system_path.read_text(encoding="utf-8")
    assert result["snapshot_id"]
    assert result["effective_after"] == "reload"
    assert "note" in result


@pytest.mark.asyncio
async def test_revise_persona_no_match(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.revise_persona({"old_text": "no such text anywhere", "new_text": "x"})
    assert result["ok"] is False
    assert result["error"] == "no_match"


@pytest.mark.asyncio
async def test_revise_persona_ambiguous_match(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    state.system_path.write_text("dup\ndup\n", encoding="utf-8")
    result = await rune.revise_persona({"old_text": "dup", "new_text": "x"})
    assert result["ok"] is False
    assert result["error"] == "ambiguous_match"


@pytest.mark.asyncio
async def test_revise_persona_never_invents_persona_file(tmp_path: Path) -> None:
    """Without a resolved system file, revise_persona fails — it must not
    invent a persona file that then silently doesn't shape the session."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    invented = rune.state.config_dir / "SYSTEM.md"
    invented.unlink()  # no system file anywhere now
    rune.state.system_path = None
    result = await rune.revise_persona({"old_text": "a", "new_text": "b"})
    assert result["ok"] is False
    assert result["error"] == "no_system_path"
    assert not invented.exists()


@pytest.mark.asyncio
async def test_revise_persona_explicit_path(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    other = tmp_path / "other.md"
    other.write_text("alpha beta", encoding="utf-8")
    result = await rune.revise_persona(
        {"old_text": "alpha", "new_text": "omega", "path": str(other)}
    )
    assert result["ok"] is True
    assert "omega beta" in other.read_text(encoding="utf-8")


# -- teach -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_teach_append_creates_named_section(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    result = await rune.teach(
        {"section": "Memory", "mode": "append", "text": "Remember the user's dog."}
    )
    assert result["ok"] is True
    assert result["changed"] is True
    content = state.system_path.read_text(encoding="utf-8")
    assert "## Memory" in content
    assert "Remember the user's dog." in content
    assert result["effective_after"] == "reload"


@pytest.mark.asyncio
async def test_teach_append_under_existing_section_no_dup_header(
    tmp_path: Path,
) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    state.system_path.write_text("# S\n\n## Memory\n\nOld note.\n", encoding="utf-8")
    result = await rune.teach({"section": "Memory", "mode": "append", "text": "New note."})
    assert result["changed"] is True
    content = state.system_path.read_text(encoding="utf-8")
    assert content.count("## Memory") == 1
    assert "Old note." in content and "New note." in content


@pytest.mark.asyncio
async def test_teach_append_duplicate_paragraph_is_noop(tmp_path: Path) -> None:
    """Bloat control: repeated teaches must not duplicate the paragraph."""
    rune, _api = make_rune(tmp_path)
    params = {"section": "Memory", "mode": "append", "text": "Same note."}
    first = await rune.teach(params)
    assert first["changed"] is True
    second = await rune.teach(params)
    assert second["ok"] is True
    assert second["changed"] is False
    state = rune.state
    assert state is not None and state.system_path is not None
    assert state.system_path.read_text(encoding="utf-8").count("Same note.") == 1


@pytest.mark.asyncio
async def test_teach_replace_uses_exactly_one_match(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    state.system_path.write_text("## Memory\n\nDogs are great.\n", encoding="utf-8")
    ok_result = await rune.teach(
        {
            "section": "Memory",
            "mode": "replace",
            "old_text": "Dogs are great.",
            "new_text": "Cats are great.",
        }
    )
    assert ok_result["ok"] is True
    assert "Cats are great." in state.system_path.read_text(encoding="utf-8")
    bad = await rune.teach(
        {
            "section": "Memory",
            "mode": "replace",
            "old_text": "missing",
            "new_text": "x",
        }
    )
    assert bad["error"] == "no_match"


@pytest.mark.asyncio
async def test_teach_invalid_mode(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.teach({"section": "Memory", "mode": "overwrite", "text": "x"})
    assert result["ok"] is False
    assert result["error"] == "invalid_mode"


@pytest.mark.asyncio
async def test_teach_creates_agent_scope_system_md(tmp_path: Path) -> None:
    """teach defaults to the resolved system_path; when none is resolved it
    creates <config_dir>/SYSTEM.md (agent scope — the discovery chain's
    lowest file-backed layer), unlike revise_persona which never invents."""
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.config_dir is not None
    (state.system_path).unlink()  # type: ignore[union-attr]
    state.system_path = None
    target = state.config_dir / "SYSTEM.md"
    assert not target.exists()
    result = await rune.teach({"section": "Memory", "mode": "append", "text": "Fresh start."})
    assert result["ok"] is True
    assert target.is_file()
    assert "## Memory" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_teach_warns_when_not_active_file(tmp_path: Path) -> None:
    """Mirrors the skills-bridge SHADOWED_SKILL rule: writing a file that
    is not the active instructions file warns loudly."""
    rune, _api = make_rune(tmp_path)
    other = tmp_path / "side-notes.md"
    result = await rune.teach(
        {"section": "Memory", "mode": "append", "text": "x", "path": str(other)}
    )
    assert result["ok"] is True
    assert "warning" in result


# -- self_snapshot ---------------------------------------------------------


@pytest.mark.asyncio
async def test_self_snapshot_captures_system_spells_config(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.config_dir is not None
    (state.config_dir / "config.json").write_text('{"a": 1}', encoding="utf-8")
    (state.spells_dir / "mine.py").write_text("def mine(): ...\n", encoding="utf-8")  # type: ignore[union-attr]
    result = await rune.self_snapshot({"label": "before-big-change"})
    assert result["ok"] is True
    snap = Path(result["path"])
    kinds = {f["kind"] for f in result["files"]}
    assert {"system", "spells", "config"} <= kinds
    assert (snap / "manifest.json").is_file()
    assert (snap / "SYSTEM.md").is_file()
    assert (snap / "spells" / "mine.py").is_file()
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["label"] == "before-big-change"
    assert manifest["agent_name"] == "test-agent"
    # extension_status surfaces the snapshot list.
    status = await rune.extension_status({})
    assert any(s["id"] == result["snapshot_id"] for s in status["snapshots"])


@pytest.mark.asyncio
async def test_self_snapshot_invalid_label(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.self_snapshot({"label": "bad/label"})
    assert result["ok"] is False
    assert result["error"] == "invalid_label"


@pytest.mark.asyncio
async def test_self_snapshot_falls_back_to_config_system_md(tmp_path: Path) -> None:
    """Fresh agent: the hook resolved no system_path, but the agent-scope
    file exists (teach created it per the spec's None rule). The snapshot
    must capture it — otherwise rollback on a fresh agent is vacuous."""
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.config_dir is not None
    state.system_path = None
    assert (state.config_dir / "SYSTEM.md").is_file()
    result = await rune.self_snapshot({})
    assert result["ok"] is True
    kinds = {f["kind"] for f in result["files"]}
    assert "system" in kinds
    snap = Path(result["path"])
    assert (snap / "SYSTEM.md").read_text(encoding="utf-8") == (
        state.config_dir / "SYSTEM.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rollback_restores_teach_on_fresh_agent(tmp_path: Path) -> None:
    """End-to-end on a fresh agent (system_path None): teach creates the
    agent-scope file, snapshot captures it, second teach, rollback
    restores the snapshot. Caught by installed-product proof."""
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.config_dir is not None
    (state.config_dir / "SYSTEM.md").unlink()
    state.system_path = None
    first = await rune.teach({"section": "Proof", "mode": "append", "text": "first lesson"})
    assert first["ok"] is True
    snap = await rune.self_snapshot({})
    assert snap["ok"] is True
    second = await rune.teach({"section": "Proof", "mode": "append", "text": "second lesson"})
    assert second["ok"] is True
    target = Path(first["path"])
    assert "second lesson" in target.read_text(encoding="utf-8")
    rolled = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert rolled["ok"] is True
    content = target.read_text(encoding="utf-8")
    assert "first lesson" in content
    assert "second lesson" not in content


@pytest.mark.asyncio
async def test_self_snapshot_cap_prunes_oldest(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    ids: list[str] = []
    for _ in range(21):
        result = await rune.self_snapshot({})
        assert result["ok"] is True
        ids.append(result["snapshot_id"])
    status = await rune.extension_status({})
    remaining = {s["id"] for s in status["snapshots"]}
    assert len(remaining) == 20
    assert ids[0] not in remaining  # oldest pruned
    assert ids[-1] in remaining


# -- self_rollback ---------------------------------------------------------


@pytest.mark.asyncio
async def test_self_rollback_restores_system_file(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    original = state.system_path.read_text(encoding="utf-8")
    snap = await rune.self_snapshot({"label": "good"})
    state.system_path.write_text("MANGLED", encoding="utf-8")
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    assert state.system_path.read_text(encoding="utf-8") == original
    assert result["pre_rollback_snapshot_id"]
    assert result["effective_after"] == "reload"


@pytest.mark.asyncio
async def test_self_rollback_unknown_snapshot(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.self_rollback({"snapshot_id": "nope-0000"})
    assert result["ok"] is False
    assert result["error"] == "unknown_snapshot"


@pytest.mark.asyncio
async def test_self_rollback_traversal_contained(tmp_path: Path) -> None:
    """Containment is checked before existence; traversal gets the same
    pinned code, never a distinct 'not found' vs 'rejected' signal."""
    rune, _api = make_rune(tmp_path)
    for evil in ("..", "../agent-config", "../../etc", "a/../../b"):
        result = await rune.self_rollback({"snapshot_id": evil})
        assert result["ok"] is False, evil
        assert result["error"] == "unknown_snapshot", evil


@pytest.mark.asyncio
async def test_self_rollback_skips_config_and_reports(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.config_dir is not None
    (state.config_dir / "config.json").write_text('{"a": 1}', encoding="utf-8")
    snap = await rune.self_snapshot({})
    (state.config_dir / "config.json").write_text('{"a": 2}', encoding="utf-8")
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    # config.json is never restored — completeness only.
    assert (state.config_dir / "config.json").read_text(encoding="utf-8") == '{"a": 2}'
    assert any("config" in s for s in result["skipped"])


@pytest.mark.asyncio
async def test_teach_concurrent_appends_no_lost_update(tmp_path: Path) -> None:
    """Two concurrent teach appends to the same file: no lost update.

    Each append must observe the file's current content at write time —
    the second writer must not overwrite the first's paragraph with a
    stale copy. Pins the observable guarantee; guards the invariant if
    the handler ever gains suspension points inside its critical section.
    """
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    p1 = {"section": "Concurrency", "mode": "append", "text": "First paragraph."}
    p2 = {"section": "Concurrency", "mode": "append", "text": "Second paragraph."}
    results = await asyncio.gather(rune.teach(p1), rune.teach(p2))
    assert all(r["ok"] and r["changed"] for r in results)
    content = state.system_path.read_text(encoding="utf-8")
    assert "First paragraph." in content
    assert "Second paragraph." in content


@pytest.mark.asyncio
async def test_revise_persona_concurrent_second_fails_no_match(tmp_path: Path) -> None:
    """Two concurrent revise_persona ops on the same old_text: the loser must
    fail with no_match, not silently overwrite the winner with stale content.

    The exactly-one-match check is a safety invariant — each op must verify
    it against the file's content as written, so a stale copy can never
    clobber a concurrent edit. Pins the observable guarantee.
    """
    rune, _api = make_rune(tmp_path)
    state = rune.state
    assert state is not None and state.system_path is not None
    state.system_path.write_text("alpha\n", encoding="utf-8")
    p1 = {"old_text": "alpha\n", "new_text": "one\n"}
    p2 = {"old_text": "alpha\n", "new_text": "two\n"}
    results = await asyncio.gather(rune.revise_persona(p1), rune.revise_persona(p2))
    oks = [r for r in results if r["ok"]]
    no_match = [r for r in results if r.get("error") == "no_match"]
    assert len(oks) == 1
    assert len(no_match) == 1
    content = state.system_path.read_text(encoding="utf-8")
    assert content in ("one\n", "two\n")
