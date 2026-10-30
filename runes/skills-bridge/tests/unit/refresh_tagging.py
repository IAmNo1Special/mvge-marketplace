from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mvge_runes_skills_bridge.rune import SkillsBridgeRune
from mvge_runes_skills_bridge.types import SkillLoad, SkillManifest


def _write_skill(skill_dir: Path, name: str, description: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nBody of {name}.",
        encoding="utf-8",
    )
    return skill_file


class _FakeCatalog:
    """Record clear/load calls like the real engine catalog."""

    def __init__(self) -> None:
        self.cleared: list[str] = []
        self.batches: list[list] = []

    def clear_rune(self, name: str) -> None:
        self.cleared.append(name)

    def load_skills(self, loads, diagnostics=None) -> None:
        self.batches.append(list(loads))


def _make_rune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SkillsBridgeRune, _FakeCatalog]:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    mock_api = MagicMock()
    mock_api.context.cwd = str(tmp_path)
    mock_api.context.agent_name = "test_agent"
    catalog = _FakeCatalog()
    mock_api._runner.skill_catalog = catalog
    return SkillsBridgeRune(mock_api), catalog


def _payload(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(cwd=str(tmp_path), agent_name="test_agent")


def test_refresh_tags_skills_bridge_and_clears_first(tmp_path: Path) -> None:
    """Refresh must tag loads so the engine can clear them on reload."""
    skill_dir = tmp_path / "skills" / "tagged-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tagged-skill\ndescription: Tagged on refresh\n---\nDo work.",
        encoding="utf-8",
    )
    # A fake catalog that records calls, like the real engine catalog.
    loaded: list = []
    cleared: list = []

    class FakeCatalog:
        def clear_rune(self, name: str) -> None:
            cleared.append(name)

        def load_skills(self, loads, diagnostics=None) -> None:
            loaded.extend(loads)

    mock_api = MagicMock()
    mock_api._runner.skill_catalog = FakeCatalog()
    rune = SkillsBridgeRune(mock_api)
    # Explicit isolation: this suite also runs standalone, where the
    # repository-root conftest never loads, so the user layer must not
    # resolve to the developer's real home (which would load whatever
    # skills happen to be installed there).
    global_dir = tmp_path / "global"
    rune.refresh_skills(agent_name="test_agent", cwd=tmp_path, global_dir=global_dir)
    assert cleared == ["skills-bridge"]
    assert [load.manifest.name for load in loaded] == ["tagged-skill"]
    assert all(load.source_rune == "skills-bridge" for load in loaded)

    # A second refresh must clear before re-adding, not duplicate.
    rune.refresh_skills(agent_name="test_agent", cwd=tmp_path, global_dir=global_dir)
    assert cleared == ["skills-bridge", "skills-bridge"]


def test_skill_load_defaults_to_no_source_rune() -> None:
    load = SkillLoad(manifest=SkillManifest(name="s", description="d"))
    assert load.source_rune is None


@pytest.mark.asyncio
async def test_rehydrate_refreshes_skills_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill added mid-session appears after rehydrate, not before."""
    rune, catalog = _make_rune(tmp_path, monkeypatch)
    assert rune.skills_map == {}
    _write_skill(tmp_path / "global" / "skills" / "added-skill", "added-skill", "Added")
    await rune.rehydrate(_payload(tmp_path))
    assert "added-skill" in rune.skills_map
    assert catalog.cleared == ["skills-bridge"]
    assert [load.manifest.name for load in catalog.batches[-1]] == ["added-skill"]
    assert all(load.source_rune == "skills-bridge" for load in catalog.batches[-1])


@pytest.mark.asyncio
async def test_rehydrate_drops_removed_skill_and_reloads_changed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rehydrate drops a deleted skill and re-reads an edited one."""
    rune, catalog = _make_rune(tmp_path, monkeypatch)
    kept_dir = tmp_path / "global" / "skills" / "kept-skill"
    gone_dir = tmp_path / "global" / "skills" / "gone-skill"
    _write_skill(kept_dir, "kept-skill", "Original description")
    _write_skill(gone_dir, "gone-skill", "Doomed description")
    await rune.on_session_start()
    assert set(rune.skills_map) == {"kept-skill", "gone-skill"}
    shutil.rmtree(gone_dir)
    _write_skill(kept_dir, "kept-skill", "Updated description")
    await rune.rehydrate(_payload(tmp_path))
    assert set(rune.skills_map) == {"kept-skill"}
    assert rune.skills_map["kept-skill"].description == "Updated description"
    assert [load.manifest.name for load in catalog.batches[-1]] == ["kept-skill"]
    assert catalog.cleared == ["skills-bridge", "skills-bridge"]


@pytest.mark.asyncio
async def test_rehydrate_serves_preserved_mtime_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preserved-mtime edit must still serve new content after rehydrate.

    This pins the manifest-cache clear in rehydrate: the mtime-keyed
    loader would otherwise re-serve the stale manifest.
    """
    rune, _catalog = _make_rune(tmp_path, monkeypatch)
    skill_dir = tmp_path / "global" / "skills" / "mtime-skill"
    skill_file = _write_skill(skill_dir, "mtime-skill", "Original description")
    await rune.on_session_start()
    assert rune.skills_map["mtime-skill"].description == "Original description"
    original = skill_file.stat()
    _write_skill(skill_dir, "mtime-skill", "Preserved-mtime description")
    os.utime(skill_file, ns=(original.st_atime_ns, original.st_mtime_ns))
    await rune.rehydrate(_payload(tmp_path))
    assert rune.skills_map["mtime-skill"].description == "Preserved-mtime description"


@pytest.mark.asyncio
async def test_rehydrate_clears_active_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activation state does not survive a reload."""
    rune, _catalog = _make_rune(tmp_path, monkeypatch)
    _write_skill(
        tmp_path / "global" / "skills" / "active-skill", "active-skill", "Active"
    )
    await rune.on_session_start()
    rune.active_skills.add("active-skill")
    await rune.rehydrate(_payload(tmp_path))
    assert rune.active_skills == set()
    assert "active-skill" in rune.skills_map


@pytest.mark.asyncio
async def test_rehydrate_payload_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None, empty-dict, and field-less payloads fall back without raising."""
    rune, _catalog = _make_rune(tmp_path, monkeypatch)
    _write_skill(
        tmp_path / "global" / "skills" / "fallback-skill", "fallback-skill", "Fallback"
    )
    await rune.rehydrate(None)
    assert "fallback-skill" in rune.skills_map
    await rune.rehydrate({})
    assert "fallback-skill" in rune.skills_map
    await rune.rehydrate(SimpleNamespace())
    assert "fallback-skill" in rune.skills_map
    await rune.rehydrate({"cwd": str(tmp_path), "agent_name": "test_agent"})
    assert "fallback-skill" in rune.skills_map


@pytest.mark.asyncio
async def test_rehydrate_falls_back_per_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payload supplying cwd but not agent_name keeps its cwd.

    The agent_name alone falls back to context, so the agent-scope skill
    is still found; and an empty-string cwd falls through to context
    cwd, so the project-scope skill is still found.
    """
    rune, _catalog = _make_rune(tmp_path, monkeypatch)
    agent_dir = tmp_path / "global" / "agents" / "test_agent" / "skills" / "agent-skill"
    _write_skill(agent_dir, "agent-skill", "Agent scope")
    _write_skill(tmp_path / "skills" / "proj-skill", "proj-skill", "Project scope")
    await rune.rehydrate(SimpleNamespace(cwd=str(tmp_path)))
    assert "agent-skill" in rune.skills_map
    await rune.rehydrate(SimpleNamespace(cwd="", agent_name="test_agent"))
    assert "proj-skill" in rune.skills_map
