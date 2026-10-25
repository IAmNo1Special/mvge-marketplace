from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvge.agent.sub_mvge import SubMvgeRecord, SubMvgeState
from mvge.core.spells import SpellContext
from mvge.runes.rune_api import RuneAPI
from mvgeos_runes_submvge_bridge.rune import rune_factory


@pytest.fixture
def mock_api() -> Any:
    api = MagicMock(spec=RuneAPI)
    registered_spells: dict[str, Any] = {}

    def register_spell(spell_def: Any) -> None:
        registered_spells[spell_def.name] = spell_def

    api.register_spell = register_spell
    api._registered = registered_spells
    api.on_dispose = MagicMock()
    return api


@pytest.fixture
def mock_sub_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.summon = AsyncMock()
    mgr.send_message = AsyncMock()
    mgr.list_active = MagicMock()
    mgr.kill = AsyncMock()
    mgr.kill_all = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_submvge_bridge_registers_spells(mock_api: Any) -> None:
    rune_factory(mock_api)
    registered = mock_api._registered
    assert "summon_mvge" in registered
    assert "message_mvge" in registered
    assert "manage_mvges" in registered


@pytest.mark.asyncio
async def test_summon_mvge_with_research_preset(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    summon_spell = mock_api._registered["summon_mvge"]

    mock_rec = SubMvgeRecord(
        sub_mvge_id="sub-abc12345",
        role="research",
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-abc.jsonl"),
        child_tome_id="tome-abc",
        workspace_path=Path("/tmp/ws"),
        state=SubMvgeState.COMPLETED,
        state_detail="Completed",
        summary="Found 3 references in codebase",
    )
    mock_sub_manager.summon.return_value = [mock_rec]

    ctx = SpellContext(spell_cast_id="cast-123")
    result = await summon_spell.execute(
        spell_cast_id="cast-123",
        params={
            "subagents": [
                {
                    "role": "research",
                    "prompt": "Find all usages of RuneAPI",
                }
            ],
            "wait": True,
        },
        ctx=ctx,
    )

    assert result["success"] is True
    assert len(result["subagents"]) == 1
    sub = result["subagents"][0]
    assert sub["sub_mvge_id"] == "sub-abc12345"
    assert sub["role"] == "research"
    assert sub["status"] == "completed"
    assert sub["summary"] == "Found 3 references in codebase"

    # Verify specs passed to manager
    call_specs = mock_sub_manager.summon.call_args[0][0]
    assert len(call_specs) == 1
    spec = call_specs[0]
    assert spec.role == "research"
    assert "read" in spec.allowed_spells
    assert "grep" in spec.allowed_spells
    assert "bash" not in spec.allowed_spells
    assert "write" not in spec.allowed_spells


@pytest.mark.asyncio
async def test_message_mvge(mock_api: Any, mock_sub_manager: MagicMock) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    message_spell = mock_api._registered["message_mvge"]

    mock_rec = SubMvgeRecord(
        sub_mvge_id="sub-abc12345",
        role="assistant",
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-abc.jsonl"),
        child_tome_id="tome-abc",
        workspace_path=Path("/tmp/ws"),
        state=SubMvgeState.COMPLETED,
        summary="Follow up done",
    )
    mock_sub_manager.send_message.return_value = mock_rec

    ctx = SpellContext(spell_cast_id="cast-456")
    result = await message_spell.execute(
        spell_cast_id="cast-456",
        params={
            "sub_mvge_id": "sub-abc12345",
            "message": "Continue analyzing",
        },
        ctx=ctx,
    )

    assert result["success"] is True
    assert result["sub_mvge_id"] == "sub-abc12345"
    assert result["status"] == "completed"
    assert result["summary"] == "Follow up done"
    mock_sub_manager.send_message.assert_awaited_once_with(
        "sub-abc12345", "Continue analyzing"
    )


@pytest.mark.asyncio
async def test_manage_mvges_actions(mock_api: Any, mock_sub_manager: MagicMock) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    manage_spell = mock_api._registered["manage_mvges"]

    rec = SubMvgeRecord(
        sub_mvge_id="sub-1",
        role="worker",
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-1.jsonl"),
        child_tome_id="tome-1",
        workspace_path=Path("/tmp/ws"),
        state=SubMvgeState.RUNNING,
    )
    mock_sub_manager.list_active.return_value = [rec]
    mock_sub_manager.kill.return_value = [rec]
    mock_sub_manager.kill_all.return_value = [rec]

    ctx = SpellContext(spell_cast_id="cast-789")

    # List
    list_res = await manage_spell.execute(
        spell_cast_id="cast-789",
        params={"action": "list"},
        ctx=ctx,
    )
    assert list_res["action"] == "list"
    assert len(list_res["subagents"]) == 1
    mock_sub_manager.list_active.assert_called_once()

    # Kill
    kill_res = await manage_spell.execute(
        spell_cast_id="cast-789",
        params={"action": "kill", "sub_mvge_ids": ["sub-1"]},
        ctx=ctx,
    )
    assert kill_res["action"] == "kill"
    mock_sub_manager.kill.assert_awaited_once_with(["sub-1"])

    # Kill all
    kill_all_res = await manage_spell.execute(
        spell_cast_id="cast-789",
        params={"action": "kill_all"},
        ctx=ctx,
    )
    assert kill_all_res["action"] == "kill_all"
    mock_sub_manager.kill_all.assert_awaited_once()
