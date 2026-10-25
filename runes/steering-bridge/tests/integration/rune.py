from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mvge_runes_steering_bridge.rune import SteeringBridgeRune, rune_factory


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _rune_with_steering(
    tmp_path: Path, global_content: str = "Global dev standard."
) -> SteeringBridgeRune:
    _write(tmp_path / "AGENTS.md", "# Project Rules\nStrict mode always.")
    global_dir = tmp_path / "global"
    _write(global_dir / "AGENTS.md", global_content)

    mock_api = MagicMock()
    mock_api.context.cwd = str(tmp_path)
    mock_api.context.agent_name = "test-agent"

    rune = SteeringBridgeRune(mock_api)
    rune.refresh_steering(cwd=tmp_path, global_dir=global_dir)
    return rune


@pytest.mark.asyncio
async def test_steering_bridge_session_start_resolves_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "AGENTS.md", "Project rules")
    global_dir = tmp_path / "global"
    _write(global_dir / "AGENTS.md", "Global rules")
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))

    mock_api = MagicMock()
    mock_api.context.cwd = str(tmp_path)
    mock_api.context.agent_name = "test-agent"

    rune = SteeringBridgeRune(mock_api)
    await rune.on_session_start()

    assert rune.state.workspace_ref is not None
    assert rune.state.workspace_ref[0] == tmp_path / "AGENTS.md"
    assert rune.state.global_ref is not None
    assert "<project_context>" in rune.state.section
    assert "Project rules" in rune.state.section
    assert "Global rules" in rune.state.section


@pytest.mark.asyncio
async def test_steering_bridge_before_mvge_start_object(
    tmp_path: Path,
) -> None:
    from mvge.runes.types import BeforeMvgeStartData

    rune = _rune_with_steering(tmp_path)
    data = BeforeMvgeStartData(
        base_prompt="Base system prompt.",
        spell_names=["bash"],
        config_dir=str(tmp_path / "config"),
        custom_prompt="",
        agent_name="test-agent",
        cwd=str(tmp_path),
    )

    out = await rune.on_before_mvge_start(data)

    assert "Base system prompt." in out.base_prompt
    assert "<project_context>" in out.base_prompt
    assert "Strict mode always." in out.base_prompt
    assert "Global dev standard." in out.base_prompt
    assert "- Project Rules: AGENTS.md" in out.base_prompt


@pytest.mark.asyncio
async def test_steering_bridge_before_mvge_start_dict_and_str(
    tmp_path: Path,
) -> None:
    rune = _rune_with_steering(tmp_path)

    dict_out = await rune.on_before_mvge_start({"base_prompt": "Base."})
    assert "<project_context>" in dict_out["base_prompt"]

    str_out = await rune.on_before_mvge_start("Bare string prompt")
    assert "Bare string prompt" in str_out
    assert "<project_context>" in str_out


@pytest.mark.asyncio
async def test_steering_bridge_before_mvge_start_dict_prompt_key(
    tmp_path: Path,
) -> None:
    rune = _rune_with_steering(tmp_path)

    legacy_out = await rune.on_before_mvge_start({"prompt": "Legacy."})
    assert "<project_context>" in legacy_out["prompt"]
    assert "Legacy." in legacy_out["prompt"]

    passthrough = await rune.on_before_mvge_start(123)
    assert passthrough == 123


@pytest.mark.asyncio
async def test_steering_bridge_empty_section_leaves_prompt_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "empty_global"))
    rune = SteeringBridgeRune()
    rune.refresh_steering(cwd=empty_repo, global_dir=tmp_path / "empty_global")

    assert rune.state.section == ""
    assert await rune.on_before_mvge_start("Unchanged") == "Unchanged"


@pytest.mark.asyncio
async def test_steering_bridge_lazy_refresh_from_event_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "AGENTS.md", "Lazy project rules.")
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "empty_global"))

    rune = SteeringBridgeRune()
    out = await rune.on_before_mvge_start(
        {"base_prompt": "Base.", "cwd": str(tmp_path)}
    )
    # Workspace steering resolves from the event cwd even without
    # a prior session_start.
    assert "Lazy project rules." in out["base_prompt"]


@pytest.mark.asyncio
async def test_steering_bridge_shutdown_clears_state(tmp_path: Path) -> None:
    rune = _rune_with_steering(tmp_path)
    assert rune.state.section != ""
    await rune.on_session_shutdown()
    assert rune.state.section == ""
    assert rune.state.workspace_ref is None


@pytest.mark.asyncio
async def test_steering_bridge_slash_commands(tmp_path: Path) -> None:
    rune = _rune_with_steering(tmp_path)

    status = await rune.handle_slash_command("")
    assert "OK:" in status
    assert "AGENTS.md" in status

    show = await rune.handle_slash_command("show")
    assert "<project_context>" in show

    valid = await rune.handle_slash_command("validate")
    assert "OK: 2 steering file(s) valid." in valid

    unknown = await rune.handle_slash_command("bogus")
    assert "Unknown steering command" in unknown

    empty_rune = SteeringBridgeRune()
    empty_rune.refresh_steering(
        cwd=tmp_path / "missing", global_dir=tmp_path / "missing_global"
    )
    missing = await empty_rune.handle_slash_command("")
    assert "MISSING:" in missing


def test_steering_bridge_validate_flags_emptied_file(tmp_path: Path) -> None:
    from mvge_runes_steering_bridge.types import SteeringState

    agents_file = _write(tmp_path / "AGENTS.md", "Original rules.")
    rune = SteeringBridgeRune()
    rune.state = SteeringState(workspace_ref=(agents_file, "AGENTS.md"))
    agents_file.write_text("   \n", encoding="utf-8")

    report = rune.validate()
    assert any(line.startswith("FAIL:") for line in report)


def test_rune_factory_registers_hooks_and_command() -> None:
    mock_api = MagicMock(spec=["on", "register_command"])

    rune = rune_factory(mock_api)
    assert isinstance(rune, SteeringBridgeRune)
    assert mock_api.on.call_count == 3
    assert mock_api.register_command.call_count == 1

    # Fallback to register_sigil on legacy API shape
    legacy_api = MagicMock(spec=["register_sigil", "register_command"])
    rune2 = rune_factory(legacy_api)
    assert isinstance(rune2, SteeringBridgeRune)
    assert legacy_api.register_sigil.call_count == 3
