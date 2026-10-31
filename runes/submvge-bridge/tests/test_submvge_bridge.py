from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvge.agent.sub_mvge import SubMvgeRecord, SubMvgeState
from mvge.core.spells import SpellContext
from mvge.runes.rune_api import RuneAPI
from mvge_runes_submvge_bridge.rune import rune_factory


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
async def test_summon_mvge_opts_out_of_dispatcher_timeout(mock_api: Any) -> None:
    """A wait:true summon runs a whole child turn, so it must not be cut
    off by the dispatcher's wall-clock spell timeout on slow lanes."""
    rune_factory(mock_api)
    assert mock_api._registered["summon_mvge"].timeout_ms == 0


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


@pytest.mark.asyncio
async def test_manage_list_formats_terminated_record(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    manage_spell = mock_api._registered["manage_mvges"]

    rec = _record(
        state=SubMvgeState.TERMINATED,
        state_detail="Terminated",
        error="Terminated",
    )
    mock_sub_manager.list_active.return_value = [rec]

    ctx = SpellContext(spell_cast_id="cast-790")
    list_res = await manage_spell.execute(
        spell_cast_id="cast-790",
        params={"action": "list"},
        ctx=ctx,
    )
    assert list_res["action"] == "list"
    assert len(list_res["subagents"]) == 1
    assert list_res["subagents"][0]["status"] == "terminated"
    assert list_res["subagents"][0]["state_detail"] == "Terminated"
    mock_sub_manager.list_active.assert_called_once()


def _record(
    sub_mvge_id: str = "sub-1",
    role: str = "worker",
    state: SubMvgeState = SubMvgeState.RUNNING,
    **extra: Any,
) -> SubMvgeRecord:
    return SubMvgeRecord(
        sub_mvge_id=sub_mvge_id,
        role=role,
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-1.jsonl"),
        child_tome_id="tome-1",
        workspace_path=Path("/tmp/ws"),
        state=state,
        **extra,
    )


@pytest.mark.asyncio
async def test_summon_explicit_allowed_spells_override_preset(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = []
    summon_spell = mock_api._registered["summon_mvge"]
    await summon_spell.execute(
        spell_cast_id="cast-100",
        params={
            "subagents": [
                {
                    "role": "research",
                    "prompt": "p",
                    "allowed_spells": ["write", "bash"],
                }
            ],
            "wait": True,
        },
        ctx=SpellContext(spell_cast_id="cast-100"),
    )
    spec = mock_sub_manager.summon.call_args[0][0][0]
    assert spec.allowed_spells == frozenset({"write", "bash"})


@pytest.mark.asyncio
async def test_summon_role_spell_defaults(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = []
    summon_spell = mock_api._registered["summon_mvge"]
    await summon_spell.execute(
        spell_cast_id="cast-101",
        params={
            "subagents": [
                {"role": "codebase_researcher", "prompt": "p"},
                {"role": "self", "prompt": "p"},
                {"role": "worker", "prompt": "p"},
                {"prompt": "p"},
            ],
            "wait": True,
        },
        ctx=SpellContext(spell_cast_id="cast-101"),
    )
    specs = mock_sub_manager.summon.call_args[0][0]
    assert len(specs) == 4
    assert "read" in specs[0].allowed_spells
    assert specs[1].allowed_spells is None
    assert specs[2].allowed_spells is None
    assert specs[3].role == "worker"
    assert specs[3].prompt == "p"


@pytest.mark.asyncio
async def test_summon_workspace_worktree_and_wait_flag(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = []
    summon_spell = mock_api._registered["summon_mvge"]
    await summon_spell.execute(
        spell_cast_id="cast-102",
        params={
            "subagents": [
                {
                    "role": "worker",
                    "prompt": "p",
                    "workspace": "worktree",
                    "model": "my-model",
                    "type_name": "other_mvge",
                }
            ],
            "wait": False,
        },
        ctx=SpellContext(spell_cast_id="cast-102"),
    )
    spec = mock_sub_manager.summon.call_args[0][0][0]
    assert spec.workspace == "worktree"
    assert spec.wait is False
    assert spec.model == "my-model"
    assert spec.agent_name == "other_mvge"
    assert mock_sub_manager.summon.call_args[1]["parent_spell_cast_id"] == "cast-102"


@pytest.mark.asyncio
async def test_summon_empty_subagents(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = []
    summon_spell = mock_api._registered["summon_mvge"]
    result = await summon_spell.execute(
        spell_cast_id="cast-103",
        params={"subagents": [], "wait": True},
        ctx=SpellContext(spell_cast_id="cast-103"),
    )
    assert result == {"success": True, "subagents": []}
    assert mock_sub_manager.summon.call_args[0][0] == []


@pytest.mark.asyncio
async def test_summon_record_formats_all_fields(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    rec = _record(
        state=SubMvgeState.ERRORED,
        state_detail="crashed",
        summary="",
        error="boom",
        mana_used=42,
    )
    mock_sub_manager.summon.return_value = [rec]
    summon_spell = mock_api._registered["summon_mvge"]
    result = await summon_spell.execute(
        spell_cast_id="cast-104",
        params={"subagents": [{"role": "worker", "prompt": "p"}]},
        ctx=SpellContext(spell_cast_id="cast-104"),
    )
    sub = result["subagents"][0]
    assert sub["status"] == "errored"
    assert sub["state_detail"] == "crashed"
    assert sub["error"] == "boom"
    assert sub["mana_used"] == 42
    assert sub["tome_path"] == "/tmp/subsessions/tome-1.jsonl"
    assert sub["child_tome_id"] == "tome-1"


@pytest.mark.asyncio
async def test_message_mvge_defaults_to_empty_strings(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.send_message.return_value = _record()
    message_spell = mock_api._registered["message_mvge"]
    result = await message_spell.execute(
        spell_cast_id="cast-105",
        params={},
        ctx=SpellContext(spell_cast_id="cast-105"),
    )
    assert result["success"] is True
    mock_sub_manager.send_message.assert_awaited_once_with("", "")


@pytest.mark.asyncio
async def test_manage_mvges_defaults_to_list_and_kill_empty(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.list_active.return_value = []
    mock_sub_manager.kill.return_value = []
    manage_spell = mock_api._registered["manage_mvges"]
    ctx = SpellContext(spell_cast_id="cast-106")
    default_res = await manage_spell.execute(
        spell_cast_id="cast-106", params={}, ctx=ctx
    )
    assert default_res["action"] == "list"
    mock_sub_manager.list_active.assert_called_once()
    kill_res = await manage_spell.execute(
        spell_cast_id="cast-106", params={"action": "kill"}, ctx=ctx
    )
    assert kill_res["action"] == "kill"
    mock_sub_manager.kill.assert_awaited_once_with([])


@pytest.mark.asyncio
async def test_manage_mvges_unknown_action_raises(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    manage_spell = mock_api._registered["manage_mvges"]
    with pytest.raises(ValueError, match="Unknown manage_mvges action: 'explode'"):
        await manage_spell.execute(
            spell_cast_id="cast-107",
            params={"action": "explode"},
            ctx=SpellContext(spell_cast_id="cast-107"),
        )


def test_get_manager_passes_runner_context_model_id(
    mock_api: Any,
) -> None:
    """model='inherit' only works when the bridge hands the manager
    the parent model: the runner context is where the agent's
    resolved model (including a runtime --model override) lives."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from mvge.runes.types import RuneContext
    from mvge_runes_submvge_bridge.rune import SubMvgeBridgeState

    mock_api._runner = SimpleNamespace(
        context=RuneContext(
            cwd="/proj", agent_name="coding_mvge", model_id="pollinations/openai"
        )
    )
    state = SubMvgeBridgeState(mock_api)
    with patch("mvge.agent.sub_mvge.SubMvgeManager") as manager_cls:
        manager = state.get_manager()
    assert manager is manager_cls.return_value
    assert manager_cls.call_args.kwargs["parent_model_id"] == "pollinations/openai"


@pytest.mark.asyncio
async def test_bridge_state_caches_manager_and_shutdown_kills_all(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    from mvge_runes_submvge_bridge.rune import SubMvgeBridgeState

    calls: list[int] = []

    def factory(**kw: Any) -> MagicMock:
        calls.append(1)
        return mock_sub_manager

    state = SubMvgeBridgeState(mock_api, manager_factory=factory)
    assert state.get_manager() is mock_sub_manager
    assert state.get_manager() is mock_sub_manager
    assert len(calls) == 1
    await state.shutdown()
    mock_sub_manager.kill_all.assert_awaited_once()
    assert state.get_manager() is mock_sub_manager
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_submvge_dispose_kills_active_submvges(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    summon_spell = mock_api._registered["summon_mvge"]
    mock_sub_manager.summon.return_value = []
    ctx = SpellContext(spell_cast_id="cast-dispose")
    await summon_spell.execute(
        spell_cast_id="cast-dispose",
        params={"subagents": [{"role": "worker", "prompt": "hi"}], "wait": True},
        ctx=ctx,
    )
    assert mock_api.on_dispose.call_count == 1
    dispose = mock_api.on_dispose.call_args[0][0]
    result = dispose()
    if result is not None:
        import inspect as _inspect

        if _inspect.isawaitable(result):
            await result
    mock_sub_manager.kill_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_summon_result_carries_effective_spells(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    """D1 test 8: the summon result reports the child's dispatch set."""
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = [_record(effective_spells=["bash"])]
    summon_spell = mock_api._registered["summon_mvge"]
    result = await summon_spell.execute(
        spell_cast_id="cast-d1-1",
        params={"subagents": [{"role": "worker", "prompt": "p"}]},
        ctx=SpellContext(spell_cast_id="cast-d1-1"),
    )
    assert result["subagents"][0]["effective_spells"] == ["bash"]


@pytest.mark.asyncio
async def test_summon_empty_allowed_spells_reports_empty_effective(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    """D1 test 8: an allowed_spells=[] child reports an explicit empty
    effective_spells list, not a missing field."""
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.return_value = [_record(effective_spells=[])]
    summon_spell = mock_api._registered["summon_mvge"]
    result = await summon_spell.execute(
        spell_cast_id="cast-d1-2",
        params={"subagents": [{"role": "worker", "prompt": "p", "allowed_spells": []}]},
        ctx=SpellContext(spell_cast_id="cast-d1-2"),
    )
    assert result["subagents"][0]["effective_spells"] == []


@pytest.mark.asyncio
async def test_summon_unknown_allowed_name_propagates_engine_error(
    mock_api: Any, mock_sub_manager: MagicMock
) -> None:
    """D1 test 8: engine validation of allowed_spells propagates through
    the bridge as the summon error; the bridge adds no validation of
    its own and must not swallow the engine error."""
    rune_factory(mock_api, manager_factory=lambda **kw: mock_sub_manager)
    mock_sub_manager.summon.side_effect = ValueError(
        "Unknown allowed spell(s): definitely_not_a_spell"
    )
    summon_spell = mock_api._registered["summon_mvge"]
    with pytest.raises(ValueError, match="definitely_not_a_spell"):
        await summon_spell.execute(
            spell_cast_id="cast-d1-3",
            params={
                "subagents": [
                    {
                        "role": "worker",
                        "prompt": "p",
                        "allowed_spells": ["definitely_not_a_spell"],
                    }
                ]
            },
            ctx=SpellContext(spell_cast_id="cast-d1-3"),
        )
