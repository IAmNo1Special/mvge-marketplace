"""Tests for the selfmod-bridge hook, registration, and command surface.

Hook payload contract (spec §5.3): attributes first with getattr defaults,
dict fallback, opaque passthrough for anything else. The parallel engine
work has not landed locally yet, so these tests drive the hook with
dataclass-like, dict, partial, and opaque payloads — the shapes the
contract promises to survive.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mvge.runes.types import ExecutionMode, SigilHook
from selfmod_bridge_conftest import FakeApi, load_root_module, make_rune, make_state

from mvgeos_runes_selfmod_bridge.prompt import build_selfmod_section
from mvgeos_runes_selfmod_bridge.rune import SelfmodBridgeRune
from mvgeos_runes_selfmod_bridge.status import describe_extensions


@dataclass
class HookPayload:
    agent_name: str = "test-agent"
    config_dir: str = ""
    runes_paths: list[str] = field(default_factory=list)
    system_path: str = ""
    base_prompt: str = "base system prompt"
    cwd: str = ""


def _payload(tmp_path: Path, **overrides: Any) -> HookPayload:
    state = make_state(tmp_path)
    base: dict[str, Any] = {
        "agent_name": "test-agent",
        "config_dir": str(state.config_dir),
        "runes_paths": [str(state.runes_paths[0])],
        "system_path": str(state.system_path),
        "base_prompt": "base system prompt",
        "cwd": str(tmp_path),
    }
    base.update(overrides)
    return HookPayload(**base)


@pytest.mark.asyncio
async def test_hook_dataclass_payload_appends_section_once(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = _payload(tmp_path)
    await rune._on_before_mvge_start(payload)
    assert "- Runes: " in payload.base_prompt
    expected = f"- Runes: {tmp_path}/runes/AGENTS.md"
    assert expected in payload.base_prompt
    # Exactly once: rehydration passes are idempotent.
    await rune._on_before_mvge_start(payload)
    assert payload.base_prompt.count("- Runes: ") == 1
    assert payload.base_prompt.count(expected) == 1


@pytest.mark.asyncio
async def test_hook_populates_state(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = _payload(tmp_path)
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.agent_name == "test-agent"
    assert rune.state.spells_dir == tmp_path / "agent-config" / "spells"
    assert rune.state.system_path == tmp_path / "agent-config" / "SYSTEM.md"
    assert rune.state.runes_paths == [tmp_path / "runes"]


@pytest.mark.asyncio
async def test_hook_dict_payload(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = {
        "agent_name": "dict-agent",
        "config_dir": str(tmp_path / "agent-config"),
        "runes_paths": [str(tmp_path / "runes")],
        "system_path": str(tmp_path / "agent-config" / "SYSTEM.md"),
        "base_prompt": "base",
        "cwd": str(tmp_path),
    }
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.agent_name == "dict-agent"
    assert "- Runes: " in payload["base_prompt"]


@pytest.mark.asyncio
async def test_hook_missing_fields_silent_no_crash(tmp_path: Path, caplog: Any) -> None:
    """Old-engine payload: no new fields — the prompt stays unchanged, but
    the bypass is not zero-signal: warning log + warning event (spec §5.3)."""
    rune, api = make_rune(tmp_path, with_state=False)
    payload: dict[str, Any] = {"base_prompt": "base"}
    with caplog.at_level("WARNING"):
        await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir is None
    assert payload["base_prompt"] == "base"
    assert ("selfmod_bridge_warning", {"stage": "bypass"}) in [
        (name, {"stage": p["stage"]}) for name, p in api.events
    ]
    assert "selfmod-bridge" in caplog.text


@pytest.mark.asyncio
async def test_hook_opaque_payload_passes_through(tmp_path: Path, caplog: Any) -> None:
    """Future/unknown payload shape: state may be empty, but the handler
    must not crash; when a section resolves and the payload cannot carry
    the prompt back, it logs a warning and emits the warning event."""
    rune, api = make_rune(tmp_path, with_state=False)

    class OpaquePayload:
        """Has extension context but no base_prompt slot and is not a
        mapping — the handler cannot write the prompt back."""

        def __init__(self) -> None:
            self.config_dir = str(tmp_path / "agent-config")
            self.runes_paths = [str(tmp_path / "runes")]

    with caplog.at_level("WARNING"):
        await rune._on_before_mvge_start(OpaquePayload())
    assert rune.state is not None
    assert any(name == "selfmod_bridge_warning" for name, _ in api.events)
    assert "selfmod-bridge" in caplog.text


@pytest.mark.asyncio
async def test_hook_missing_system_file_bypass_warns(tmp_path: Path, caplog: Any) -> None:
    """No runes paths and no system file: the prompt is unchanged, but the
    bypass emits the warning event + log (spec §5.3 — not zero signal)."""
    rune, api = make_rune(tmp_path, with_state=False)
    payload = _payload(
        tmp_path,
        system_path=str(tmp_path / "nonexistent" / "SYSTEM.md"),
        runes_paths=[],
    )
    with caplog.at_level("WARNING"):
        await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.system_path is None
    assert payload.base_prompt == "base system prompt"
    assert any(
        name == "selfmod_bridge_warning" and p["stage"] == "bypass" for name, p in api.events
    )
    assert "selfmod-bridge" in caplog.text


@pytest.mark.asyncio
async def test_hook_spells_dir_prefers_agent_over_cwd(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    cwd_spells = tmp_path / "spells"
    cwd_spells.mkdir()
    payload = _payload(tmp_path)
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir == tmp_path / "agent-config" / "spells"


@pytest.mark.asyncio
async def test_hook_spells_dir_falls_back_to_payload_cwd(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = _payload(tmp_path, cwd=str(tmp_path))
    (tmp_path / "agent-config" / "spells").rmdir()
    (tmp_path / "spells").mkdir()
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir == tmp_path / "spells"


@pytest.mark.asyncio
async def test_hook_prefers_payload_spells_dir_over_heuristic(tmp_path: Path) -> None:
    """Spec §4.3/§5.3: the engine resolves the ACTIVE spells dir once; the
    rune reads the payload's ``spells_dir`` instead of re-deriving the
    branch logic. A payload-carried spells dir wins over the
    config_dir/spells heuristic."""
    rune, _api = make_rune(tmp_path, with_state=False)
    engine_spells = tmp_path / "engine-resolved" / "spells"
    engine_spells.mkdir(parents=True)
    payload = {
        "agent_name": "test-agent",
        "config_dir": str(tmp_path / "agent-config"),
        "runes_paths": [str(tmp_path / "runes")],
        "system_path": str(tmp_path / "agent-config" / "SYSTEM.md"),
        "spells_dir": str(engine_spells),
        "base_prompt": "base",
        "cwd": str(tmp_path),
    }
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir == engine_spells


@pytest.mark.asyncio
async def test_hook_payload_spells_dir_must_be_a_dir(tmp_path: Path) -> None:
    """A payload ``spells_dir`` that is not an existing dir is unusable —
    fall back to the heuristic rather than pointing at a bogus path."""
    rune, _api = make_rune(tmp_path, with_state=False)
    (tmp_path / "agent-config" / "spells").mkdir(parents=True)
    payload = {
        "agent_name": "test-agent",
        "config_dir": str(tmp_path / "agent-config"),
        "runes_paths": [str(tmp_path / "runes")],
        "system_path": str(tmp_path / "agent-config" / "SYSTEM.md"),
        "spells_dir": str(tmp_path / "does-not-exist" / "spells"),
        "base_prompt": "base",
        "cwd": str(tmp_path),
    }
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir == tmp_path / "agent-config" / "spells"


@pytest.mark.asyncio
async def test_hook_absent_payload_spells_dir_uses_heuristic(tmp_path: Path) -> None:
    """Old-engine payload without ``spells_dir``: graceful degradation to
    the config_dir/cwd heuristic."""
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = _payload(tmp_path)
    await rune._on_before_mvge_start(payload)
    assert rune.state is not None
    assert rune.state.spells_dir == tmp_path / "agent-config" / "spells"


# -- registration ----------------------------------------------------------


def test_factory_and_registration(tmp_path: Path) -> None:
    root_rune_factory = load_root_module("rune").rune_factory

    api = FakeApi()
    # The engine instantiates runes as factory(api) — one arg (runner.py:667)
    # — and never calls register() itself, so the factory must register.
    rune = root_rune_factory(api)
    assert isinstance(rune, SelfmodBridgeRune)
    assert rune.version == "0.1.0"

    assert SigilHook.BEFORE_MVGE_START in api.hooks
    assert "selfmod" in api.commands
    assert len(api.spells) == 8

    # The registered command must carry a string description and the real
    # handler — the engine builds the CLI command's help text from the
    # description and dispatches through the handler. A handler passed in
    # the description slot breaks `mvgeos selfmod --help` and dispatches
    # nothing (caught by installed CLI proof).
    description, handler = api.commands["selfmod"]
    assert isinstance(description, str) and description
    assert handler == rune.handle_selfmod_command

    # Explicit re-registration is a no-op: the handler must not be replaced.
    handler_before = api.hooks[SigilHook.BEFORE_MVGE_START]
    rune.register()
    assert api.hooks[SigilHook.BEFORE_MVGE_START] is handler_before

    expected_read_only = {
        "scaffold_spell": False,
        "scaffold_rune": False,
        "scaffold_skill": False,
        "extension_status": True,
        "revise_persona": False,
        "teach": False,
        "self_snapshot": True,
        "self_rollback": False,
    }
    for name, read_only in expected_read_only.items():
        spell = api.spells[name]
        assert spell.read_only is read_only, name
        assert spell.execution_mode == ExecutionMode.PARALLEL, name
        assert inspect.iscoroutinefunction(spell._handler), name
        # Mutating handlers are audit-wrapped (marked with the op name);
        # the read-only status handler stays the raw bound method.
        if read_only and name == "extension_status":
            assert getattr(spell._handler, "__self__", None) is rune, name
        else:
            assert getattr(spell._handler, "_audit_op", None) == name, name

    assert api.widened == [name for name, _, _ in SelfmodBridgeRune._SPELLS]


def test_root_rune_reexports_factory() -> None:
    root_rune = load_root_module("rune")

    assert callable(root_rune.rune_factory)
    assert callable(root_rune.create_rune)
    assert root_rune.SelfmodBridgeRune is SelfmodBridgeRune


# -- command ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_status_shares_describe_extensions(tmp_path: Path) -> None:

    rune, _api = make_rune(tmp_path)
    text = await rune.handle_selfmod_command("status")
    status = describe_extensions(rune.state)  # type: ignore[arg-type]
    assert "test-agent" in text
    assert status["config_dir"] in text
    assert "snapshots (0):" in text


@pytest.mark.asyncio
async def test_command_show_shares_section_builder(tmp_path: Path) -> None:

    rune, _api = make_rune(tmp_path)
    text = await rune.handle_selfmod_command("show")
    section = build_selfmod_section(rune.state.runes_paths, rune.state.system_path)  # type: ignore[union-attr]
    assert text == section


@pytest.mark.asyncio
async def test_command_defaults_to_status(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert await rune.handle_selfmod_command("") == await rune.handle_selfmod_command("status")


@pytest.mark.asyncio
async def test_command_unknown_verb_shows_usage(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert "usage" in await rune.handle_selfmod_command("bogus")


@pytest.mark.asyncio
async def test_command_state_none_is_loud(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path, with_state=False)
    assert "not initialized" in await rune.handle_selfmod_command("status")
    assert "not initialized" in await rune.handle_selfmod_command("show")


@pytest.mark.asyncio
async def test_cli_delegates_to_rune(tmp_path: Path) -> None:
    root_cli = load_root_module("cli")
    SelfmodCLI = root_cli.SelfmodCLI

    rune, _api = make_rune(tmp_path)
    cli = SelfmodCLI(rune)
    assert await cli.handle("status") == await rune.handle_selfmod_command("status")


@pytest.mark.asyncio
async def test_hook_guard_uses_section_marker_not_content_line(
    tmp_path: Path,
) -> None:
    """The exact-once guard keys on the normative section marker. Unrelated
    prose containing "- Runes: " must not suppress the section, and the
    section is never appended twice."""
    rune, _api = make_rune(tmp_path, with_state=False)
    payload = _payload(tmp_path)
    payload.base_prompt = "Notes: I once wrote - Runes: by hand in prose."
    await rune._on_before_mvge_start(payload)
    assert payload.base_prompt.count("Self-Modification & Customization:") == 1
    # Rehydration pass: still exactly one section.
    await rune._on_before_mvge_start(payload)
    assert payload.base_prompt.count("Self-Modification & Customization:") == 1


@pytest.mark.asyncio
async def test_spells_execute_through_real_spell_definition(
    tmp_path: Path,
) -> None:
    """The engine invokes spells via ``SpellDefinition.execute(cast_id,
    params)`` — which passes ``signal=``/``on_update=`` kwargs to the
    handler. Every spell must survive that path, and every definition
    must carry an explicit parameter schema (the engine does not derive
    one from the handler signature)."""
    rune, _api = make_rune(tmp_path)
    defs = {d.name: d for d in rune._spell_definitions()}
    assert set(defs) == {
        "scaffold_spell",
        "scaffold_rune",
        "scaffold_skill",
        "extension_status",
        "revise_persona",
        "teach",
        "self_snapshot",
        "self_rollback",
    }
    for name, d in defs.items():
        assert d.parameters.get("type") == "object", name
        assert isinstance(d.parameters.get("properties"), dict), name

    # Read-only spells through the real path.
    status = await defs["extension_status"].execute("cast-1", {})
    assert status["ok"] is True
    assert "runes_paths" in status

    snap = await defs["self_snapshot"].execute("cast-2", {"label": "via-exec"})
    assert snap["ok"] is True

    # Mutating spell through the real path, with signal/on_update kwargs
    # as the engine passes them.
    result = await defs["scaffold_spell"].execute(
        "cast-3",
        {"name": "exec_spell", "description": "via engine path"},
        signal=None,
        on_update=None,
    )
    assert result["ok"] is True
    assert result["path"].endswith("exec_spell.py")

    taught = await defs["teach"].execute(
        "cast-4",
        {"section": "Exec", "mode": "append", "text": "hello via exec"},
    )
    assert taught["ok"] is True
    assert taught["changed"] is True
