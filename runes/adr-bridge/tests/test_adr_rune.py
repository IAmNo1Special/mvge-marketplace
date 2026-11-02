"""Unit and integration tests for adr-bridge rune lifecycle and spells."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mvge.runes.types import BeforeMvgeStartData, SigilHook, SpellDefinition
from mvge_runes_adr_bridge.rune import rune_factory


class MockRuneAPI:
    def __init__(self, cwd: Path) -> None:
        self.context = MagicMock()
        self.context.cwd = str(cwd)
        self.hooks: dict[SigilHook, list] = {}
        self.spells: dict[str, SpellDefinition] = {}
        self.commands: dict[str, dict] = {}

    def on(self, hook: SigilHook, handler) -> None:
        self.hooks.setdefault(hook, []).append(handler)

    def register_spell(self, spell: SpellDefinition) -> None:
        self.spells[spell.name] = spell

    def register_command(self, name: str, description: str, handler) -> None:
        self.commands[name] = {"description": description, "handler": handler}


@pytest.mark.asyncio
async def test_adr_rune_lifecycle_and_spells(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0001-microkernel.md").write_text(
        "# Microkernel Architecture\n\n"
        "* Status: accepted\n"
        "* Date: 2026-09-01\n\n"
        "## Context and Problem Statement\n\n"
        "Clean boundaries needed.\n\n"
        "## Considered Options\n\n"
        "* Option A\n\n"
        "## Decision Outcome\n\n"
        "Chosen option: Option A, because modularity.\n",
        encoding="utf-8",
    )

    api = MockRuneAPI(cwd=tmp_path)
    rune_factory(api)

    # Verify hooks
    assert SigilHook.SESSION_START in api.hooks
    assert SigilHook.BEFORE_MVGE_START in api.hooks
    assert SigilHook.CONTEXT_TRANSFORM in api.hooks
    assert SigilHook.SESSION_SHUTDOWN in api.hooks

    # Verify spells
    assert "adr_list" in api.spells
    assert "adr_get" in api.spells
    assert "adr_validate" in api.spells
    assert "adr_new" in api.spells

    # Verify command
    assert "adr" in api.commands

    # 1. Test before_mvge_start
    before_hook = api.hooks[SigilHook.BEFORE_MVGE_START][0]
    data = BeforeMvgeStartData(
        base_prompt="Base prompt:",
        spell_names=[],
        config_dir=str(tmp_path),
        custom_prompt="",
        agent_name="test",
        cwd=str(tmp_path),
    )
    res_data = await before_hook(data)
    assert "<architectural_decisions" in res_data.base_prompt
    assert "0001" in res_data.base_prompt

    # 2. Test adr_list spell
    list_handler = api.spells["adr_list"]._handler
    list_json = await list_handler({})
    list_data = json.loads(list_json)
    assert len(list_data) == 1
    assert list_data[0]["number"] == 1
    assert list_data[0]["title"] == "Microkernel Architecture"

    # 3. Test adr_get spell
    get_handler = api.spells["adr_get"]._handler
    get_json = await get_handler({"number": 1})
    get_data = json.loads(get_json)
    assert get_data["number"] == 1
    assert "Clean boundaries" in get_data["context_and_problem_statement"]

    # Test adr_get missing
    missing_json = await get_handler({"number": 999})
    assert "not found" in missing_json

    # 4. Test adr_validate spell
    val_handler = api.spells["adr_validate"]._handler
    val_json = await val_handler({})
    val_data = json.loads(val_json)
    assert val_data["valid"] is True
    assert val_data["records_checked"] == 1

    # 5. Test adr_new spell
    new_handler = api.spells["adr_new"]._handler
    new_json = await new_handler(
        {"title": "Postgres Persistence", "context": "Need SQL storage"}
    )
    new_data = json.loads(new_json)
    assert new_data["status"] == "created"
    assert new_data["filename"] == "0002-postgres-persistence.md"

    # Test adr_new empty title
    err_json = await new_handler({"title": ""})
    assert "error" in err_json

    # 6. Test /adr command
    cmd_handler = api.commands["adr"]["handler"]
    list_out = await cmd_handler("list")
    assert "FOUND: 2 Architectural Decision Records" in list_out

    get_out = await cmd_handler("get 1")
    assert "ADR 0001: Microkernel Architecture" in get_out

    val_out = await cmd_handler("validate")
    assert "OK: All 2 ADRs conform" in val_out

    sync_out = await cmd_handler("sync")
    assert "OK: Synchronized" in sync_out

    new_cmd_out = await cmd_handler("new Redis Cache")
    assert "OK: Scaffolding created" in new_cmd_out

    unknown_out = await cmd_handler("unknown")
    assert "Unknown adr command" in unknown_out

    # 7. Test context transform
    transform_hook = api.hooks[SigilHook.CONTEXT_TRANSFORM][0]
    inv_list = [{"role": "user", "content": "Query"}]
    transformed = await transform_hook(inv_list)
    assert len(transformed) == 1
    assert "<architectural_decisions" in transformed[0]["content"]

    assert await transform_hook("non-list") == "non-list"

    # 8. Test shutdown
    shutdown_hook = api.hooks[SigilHook.SESSION_SHUTDOWN][0]
    await shutdown_hook()
