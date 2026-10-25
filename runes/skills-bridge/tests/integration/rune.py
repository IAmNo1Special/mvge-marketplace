from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mvge_runes_skills_bridge.rune import SkillsBridgeRune, rune_factory
from mvge_runes_skills_bridge.types import SkillManifest


@pytest.mark.asyncio
async def test_skills_bridge_rune_lifecycle(tmp_path: Path) -> None:
    skill_dir = tmp_path / "lifecycle-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: lifecycle-skill\ndescription: Tests rune lifecycle\n---\nDo work.",
        encoding="utf-8",
    )

    mock_api = MagicMock()
    mock_api.context.cwd = str(tmp_path)
    mock_api.context.agent_name = "test_agent"

    rune = SkillsBridgeRune(mock_api)
    await rune.on_session_start()
    # Manually register the test skill in skills_map
    m = SkillManifest(
        name="lifecycle-skill",
        description="Tests rune lifecycle",
        path=str(skill_dir),
        body="Do work.",
    )
    rune.skills_map["lifecycle-skill"] = m

    # BEFORE_MVGE_START with dict
    data = {"prompt": "Base system prompt"}
    augmented = await rune.on_before_mvge_start(data)
    assert "<available_skills>" in augmented["prompt"]
    assert "<name>lifecycle-skill</name>" in augmented["prompt"]

    # BEFORE_MVGE_START with str
    str_augmented = await rune.on_before_mvge_start("Bare string prompt")
    assert "<available_skills>" in str_augmented

    # BEFORE_MVGE_START with suppressed
    rune.suppress_catalog = True
    suppressed = await rune.on_before_mvge_start("Unchanged")
    assert suppressed == "Unchanged"
    rune.suppress_catalog = False

    # CONTEXT_TRANSFORM
    invocations = [{"role": "user", "content": "Hello"}]
    transformed = await rune.on_context_transform(invocations)
    assert "<available_skills>" in transformed[0]["content"]

    # ACTIVATE_SKILL SPELL
    res = await rune.handle_activate_skill(params={"name": "lifecycle-skill"})
    assert res["name"] == "lifecycle-skill"
    assert '<skill_content name="lifecycle-skill">' in res["content"]
    assert "Do work." in res["content"]

    # Deduplicated activation
    res_repeat = await rune.handle_activate_skill(params={"name": "lifecycle-skill"})
    assert 'already_active="true"' in res_repeat["content"]

    # ACTIVATE_SKILL with missing name
    err_res = await rune.handle_activate_skill(params={})
    assert "error" in err_res

    # ACTIVATE_SKILL with dict cast_id
    dict_res = await rune.handle_activate_skill(
        spell_cast_id={"name": "lifecycle-skill"}
    )
    assert dict_res["name"] == "lifecycle-skill"

    # SESSION_SHUTDOWN
    await rune.on_session_shutdown()
    assert len(rune.active_skills) == 0


@pytest.mark.asyncio
async def test_skills_bridge_slash_commands(tmp_path: Path) -> None:
    rune = SkillsBridgeRune()

    # /skills when empty
    empty_out = await rune.handle_slash_command("")
    assert "MISSING:" in empty_out

    m = SkillManifest(
        name="alpha", description="First skill", path=str(tmp_path / "alpha")
    )
    rune.skills_map["alpha"] = m

    # /skills (no args)
    list_out = await rune.handle_slash_command("")
    assert "Available skills: alpha" in list_out

    # /skill alpha
    act_out = await rune.handle_slash_command("alpha")
    assert "OK: Skill 'alpha' activated." in act_out

    # /skill nonexistent
    fail_out = await rune.handle_slash_command("nonexistent")
    assert "FAIL: Skill 'nonexistent' not found" in fail_out


def test_rune_factory_registers_components() -> None:
    mock_api = MagicMock(spec=["on", "register_spell", "register_command"])

    rune = rune_factory(mock_api)
    assert isinstance(rune, SkillsBridgeRune)
    assert mock_api.on.call_count == 4
    assert mock_api.register_spell.call_count == 1
    assert mock_api.register_command.call_count == 2

    # Test fallback to register_sigil
    legacy_api = MagicMock(
        spec=["register_sigil", "register_spell", "register_command"]
    )
    rune2 = rune_factory(legacy_api)
    assert isinstance(rune2, SkillsBridgeRune)
    assert legacy_api.register_sigil.call_count == 4
