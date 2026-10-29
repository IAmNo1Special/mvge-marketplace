from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from mvge_runes_skills_bridge.rune import SkillsBridgeRune
from mvge_runes_skills_bridge.types import SkillLoad, SkillManifest


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
