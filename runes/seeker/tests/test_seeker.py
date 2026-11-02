from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvge.agent.harness import MvgeHarness
from mvge.agent.harness._state import MvgeState
from mvge.core.abort import AbortSignal
from mvge.core.events import ContemplationLevel
from mvge.core.invocations import SummonerRequest
from mvge.core.spells import MvgeSpell, SpellContext, SpellExecutionMode
from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import AfterSpellResultData, RuneLoad, SigilHook


@pytest.mark.asyncio
async def test_seeker_rune_load() -> None:
    rune_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.name == "seeker"

    diags: list[Any] = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diags)
    assert factory is not None
    assert len(diags) == 0

    runner = RuneRunner()
    load = RuneLoad(manifest=manifest, factory=factory)
    await runner.load_rune_loads([load])

    spells = [s.name for s in runner.get_all_registered_spells()]
    assert "tool_search" in spells
    assert "skill_search" in spells
    assert "skill_execute" in spells
    assert "mcp_search" in spells
    assert "search" in spells
    assert "activate" in spells
    assert "execute" in spells
    assert "unload" in spells
    assert runner.get_active_spells() == [
        "activate",
        "execute",
        "mcp_search",
        "search",
        "skill_execute",
        "skill_search",
        "tool_search",
        "unload",
    ]


@pytest.mark.asyncio
async def test_seeker_declares_spell_gateway() -> None:
    rune_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    if not hasattr(manifest, "spell_gateway"):
        pytest.skip("engine predates spell_gateway support")
    assert manifest.spell_gateway is True

    diags: list[Any] = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diags)
    assert factory is not None

    runner = RuneRunner()
    await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
    assert runner.gateway_rune_name == "seeker"
    assert runner.gateway_spell_names() == [
        "activate",
        "execute",
        "mcp_search",
        "search",
        "skill_execute",
        "skill_search",
        "tool_search",
        "unload",
    ]


@pytest.mark.asyncio
async def test_seeker_session_start_does_not_touch_global_allowlist() -> None:
    """The engine owns the global filter (narrows it at load from the
    manifest flag); the rune must not set, replace, or drop it."""
    rune_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diags: list[Any] = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diags)
    assert factory is not None

    runner = RuneRunner()
    await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])

    await runner.emit_async(SigilHook.SESSION_START, {"session_name": "s"})
    assert runner.get_global_spell_allowlist() is None


@pytest.mark.asyncio
async def test_tool_search_widens_global_allowlist(tmp_path: Path) -> None:
    from mvge_runes_seeker.router import SpellFileMatch
    from mvge_runes_seeker.spell import ToolSearchSpell

    rune_api = MagicMock()
    rune_api.widen_global_allowlist = MagicMock()
    # An explicit, existing root: tool_search reports a missing search root
    # instead of routing, so a Spell with no reachable root never reaches
    # the widening this test is about.
    (tmp_path / "grimoire").mkdir()
    spell = ToolSearchSpell(
        stream_text=None,
        spells_root=tmp_path,
        agent_name="test",
        rune_api=rune_api,
    )
    matches = [
        SpellFileMatch(
            source_path=Path("g/tool_a.py"), grimoire="g", matched_context="c"
        ),
        SpellFileMatch(
            source_path=Path("g/tool_b.py"), grimoire="g", matched_context="c"
        ),
    ]
    with (
        patch("mvge_runes_seeker.spell.DCIRouter") as mock_router_cls,
        patch("mvge_runes_seeker.spell.NLTSelector") as mock_selector_cls,
    ):
        mock_router_cls.return_value.route = AsyncMock(return_value=matches)
        mock_selector_cls.return_value.select = AsyncMock(return_value=matches)
        spell._spell_registry.load_selected = AsyncMock(
            return_value=[
                {"name": "tool_a", "description": "a", "parameters": {}},
                {"name": "tool_b", "description": "b", "parameters": {}},
            ]
        )
        result = await spell.execute("cast1", {"operation": "unrelated query xyz"})

    assert result["spells_found"] == 2
    rune_api.widen_global_allowlist.assert_called_once_with(["tool_a", "tool_b"])


@pytest.mark.asyncio
async def test_connector_registers_mcp_spells_under_seeker() -> None:
    from mvge_runes_seeker.connector import MCPConnector
    from mvge_runes_seeker.discovery import MCPServerInfo

    rune_api = MagicMock()
    info = MCPServerInfo(
        name="srv",
        config={"command": "npx", "transport": "stdio"},
        source_file=Path("srv.mcp.json"),
    )
    connector = MCPConnector(info, rune_api=rune_api)
    connector.list_tools = AsyncMock(
        return_value=[{"name": "t", "description": "d", "inputSchema": {}}]
    )
    connector.list_resources = AsyncMock(return_value=[])
    connector.list_prompts = AsyncMock(return_value=[])
    await connector.register_all_capabilities()
    registered = [c.args[0].name for c in rune_api.register_spell.call_args_list]
    assert registered == ["mcp_t"]


@pytest.mark.asyncio
async def test_search_grimoires_matches_hint(tmp_path: Path) -> None:
    from mvge_runes_seeker.router import DCIRouter

    (tmp_path / "my_grimoire").mkdir()
    (tmp_path / "other").mkdir()
    router = DCIRouter(spells_root=tmp_path)
    assert await router._search_grimoires("my grimoire") == [tmp_path / "my_grimoire"]
    assert await router._search_grimoires("") == []


# --- Seeker idle-clock fixes (D2 hook crash + D3 activation stamp) ---


async def _load_seeker_runner() -> RuneRunner:
    rune_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diags: list[Any] = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diags)
    assert factory is not None
    assert len(diags) == 0
    runner = RuneRunner()
    await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
    return runner


def _spell_by_name(runner: RuneRunner, name: str) -> Any:
    for spell in runner.get_all_registered_spells():
        if spell.name == name:
            return spell
    raise AssertionError(f"spell {name!r} not registered")


def _no_after_spell_result_diagnostics(runner: RuneRunner) -> list[Any]:
    return [
        d
        for d in runner.diagnostics
        if "after_spell_result" in getattr(d, "message", "")
    ]


# D2 test 1: exact triggering path — real dataclass payload via emit_chain.
@pytest.mark.asyncio
async def test_after_spell_result_accepts_typed_payload() -> None:
    runner = await _load_seeker_runner()
    payload = AfterSpellResultData(spell_name="search", spell_cast_id="c1", result={})
    result = await runner.emit_chain(SigilHook.AFTER_SPELL_RESULT, payload)
    assert result is payload
    assert _no_after_spell_result_diagnostics(runner) == []


# D2 test 2: the TTL clock is actually populated (aged cast, not turn 0).
@pytest.mark.asyncio
async def test_cast_spell_clock_survives_seven_turn_ends_then_evicts() -> None:
    runner = await _load_seeker_runner()
    # Age the session before the cast: with no allowlist set, TURN_END still
    # advances the ledger clock (it returns before eviction only).
    for _ in range(5):
        await runner.emit_async(SigilHook.TURN_END, {})
    runner.set_global_spell_allowlist([*runner.gateway_spell_names(), "some_spell"])
    await runner.emit_chain(
        SigilHook.AFTER_SPELL_RESULT,
        AfterSpellResultData(spell_name="some_spell", spell_cast_id="c1", result={}),
    )
    for _ in range(7):
        await runner.emit_async(SigilHook.TURN_END, {})
        assert "some_spell" in (runner.get_global_spell_allowlist() or [])
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "some_spell" not in (runner.get_global_spell_allowlist() or [])


# D2 test 3: dict-shape tolerance (guards the dual-shape helper choice).
@pytest.mark.asyncio
async def test_after_spell_result_accepts_dict_payload() -> None:
    runner = await _load_seeker_runner()
    payload = {"spell_name": "search"}
    result = await runner.emit_chain(SigilHook.AFTER_SPELL_RESULT, payload)
    assert result is payload
    assert _no_after_spell_result_diagnostics(runner) == []


# D2 test 4: no payload poisoning — downstream handlers still see the dataclass.
@pytest.mark.asyncio
async def test_after_spell_result_does_not_replace_chained_payload() -> None:
    runner = await _load_seeker_runner()
    seen: list[Any] = []
    runner.register_handler(
        SigilHook.AFTER_SPELL_RESULT, lambda data: seen.append(data)
    )
    payload = AfterSpellResultData(spell_name="search", spell_cast_id="c1", result={})
    result = await runner.emit_chain(SigilHook.AFTER_SPELL_RESULT, payload)
    assert result is payload
    assert seen == [payload]
    assert isinstance(seen[0], AfterSpellResultData)


async def _aged_activated_runner(
    runner: RuneRunner, spell_name: str, current_turn: int = 20
) -> Any:
    """Seed an aged ledger, activate via the real spell, return the spell."""
    activate = _spell_by_name(runner, "activate")
    ledger = activate._ledger
    ledger.current_turn = current_turn
    ledger.discovered_spells.add(spell_name)
    runner.set_global_spell_allowlist([*runner.gateway_spell_names(), spell_name])
    result = await activate.execute("cast1", {"spells": [spell_name]})
    assert result["status"] == "ok"
    assert spell_name in result["activated_spells"]
    assert spell_name in (runner.get_global_spell_allowlist() or [])
    return activate


# D3 test 1: activated spell survives the turn end in an old session.
@pytest.mark.asyncio
async def test_activated_spell_survives_turn_end_in_old_session() -> None:
    runner = await _load_seeker_runner()
    activate = await _aged_activated_runner(runner, "manage_mvges")
    assert activate._ledger.last_active_turn["manage_mvges"] == 20
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "manage_mvges" in (runner.get_global_spell_allowlist() or [])


# D3 test 2: activated spell still expires after the TTL, not before.
# (Annotation corrected per review: this FAILS today via immediate eviction,
# not "passes today for the wrong reason".)
@pytest.mark.asyncio
async def test_activated_spell_expires_after_ttl() -> None:
    runner = await _load_seeker_runner()
    await _aged_activated_runner(runner, "manage_mvges")
    for _ in range(7):
        await runner.emit_async(SigilHook.TURN_END, {})
        assert "manage_mvges" in (runner.get_global_spell_allowlist() or [])
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "manage_mvges" not in (runner.get_global_spell_allowlist() or [])


# D3 test 5: connected MCP tool names are stamped on activation.
@pytest.mark.asyncio
async def test_mcp_tool_activation_stamps_clock() -> None:
    runner = await _load_seeker_runner()
    activate = _spell_by_name(runner, "activate")
    ledger = activate._ledger
    ledger.current_turn = 20
    ledger.discovered_mcps.add("srv")
    runner.set_global_spell_allowlist([*runner.gateway_spell_names()])

    class FakeDiscovery:
        async def search(self, query: str) -> list[Any]:
            return [SimpleNamespace(name="srv")]

    class FakeConnector:
        def __init__(self, info: Any, runner: Any = None, rune_api: Any = None) -> None:
            self.info = info

        async def connect(self) -> None:
            return None

        async def register_all_capabilities(self) -> list[Any]:
            return [SimpleNamespace(name="mcp_srv_tool")]

    activate._discovery = FakeDiscovery()
    with patch("mvge_runes_seeker.activate_spell.MCPConnector", FakeConnector):
        result = await activate.execute("cast1", {"mcp_servers": ["srv"]})
    assert result["status"] == "ok"
    assert result["connected_mcp_tools"] == ["mcp_srv_tool"]
    assert ledger.last_active_turn["mcp_srv_tool"] == 20
    assert "mcp_srv_tool" in (runner.get_global_spell_allowlist() or [])
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "mcp_srv_tool" in (runner.get_global_spell_allowlist() or [])


# D3 test 6: gateway spells stay exempt in an aged session.
@pytest.mark.asyncio
async def test_gateway_spells_still_exempt_in_old_session() -> None:
    runner = await _load_seeker_runner()
    gateway = runner.gateway_spell_names()
    runner.set_global_spell_allowlist(list(gateway))
    for _ in range(10):
        await runner.emit_async(SigilHook.TURN_END, {})
    assert runner.get_global_spell_allowlist() == gateway


class _ManageMvgesStub(MvgeSpell):
    """Stand-in for the submvge-bridge spell in the harness-level test."""

    def __init__(self) -> None:
        super().__init__(
            name="manage_mvges",
            description="List sub-Mvges",
            parameters={
                "type": "object",
                "properties": {"action": {"enum": ["list"]}},
                "required": ["action"],
            },
            execution_mode=SpellExecutionMode.PARALLEL,
        )

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"sub_mvges": []}


# D3 engine integration (marketplace layout): aged session, real seeker
# eviction wired in, harness rebuild must keep the activated spell.
@pytest.mark.asyncio
async def test_harness_rebuild_keeps_activated_spell_in_aged_session() -> None:
    runner = await _load_seeker_runner()
    stub = _ManageMvgesStub()
    await _aged_activated_runner(runner, "manage_mvges")
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "manage_mvges" in (runner.get_global_spell_allowlist() or [])

    spells: list[Any] = [
        s
        for s in runner.get_all_registered_spells()
        if s.name in ("search", "activate")
    ]
    state = MvgeState(
        system_prompt="Test prompt",
        spells=[*spells, stub],
        invocations=[SummonerRequest(role="user", content="Test prompt")],
        contemplation_level=ContemplationLevel.OFF,
        rune_runner=runner,
    )
    harness = MvgeHarness(state=state, discovered_spells=[stub])
    harness.reload_spells()
    assert "manage_mvges" in [s.name for s in harness.state.spells]
