from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneLoad, SigilHook


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
    from mvge.provider.registry import RealmRegistry
    from mvgeos_runes_seeker.router import SpellFileMatch
    from mvgeos_runes_seeker.spell import ToolSearchSpell

    rune_api = MagicMock()
    rune_api.widen_global_allowlist = MagicMock()
    # An explicit, existing root: tool_search reports a missing search root
    # instead of routing, so a Spell with no reachable root never reaches
    # the widening this test is about.
    (tmp_path / "grimoire").mkdir()
    spell = ToolSearchSpell(
        provider_registry=RealmRegistry(),
        spells_root=tmp_path,
        agent_name="test",
        nlt_api_key="",
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
        patch("mvgeos_runes_seeker.spell.DCIRouter") as mock_router_cls,
        patch("mvgeos_runes_seeker.spell.NLTSelector") as mock_selector_cls,
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
    from mvgeos_runes_seeker.connector import MCPConnector
    from mvgeos_runes_seeker.discovery import MCPServerInfo

    rune_api = MagicMock()
    info = MCPServerInfo(
        name="srv",
        config={"command": "npx", "transport": "stdio"},
        source_file=Path("srv.mcp.json"),
    )
    connector = MCPConnector(info, rune_runner=None, rune_api=rune_api)
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
    from mvgeos_runes_seeker.router import DCIRouter

    (tmp_path / "my_grimoire").mkdir()
    (tmp_path / "other").mkdir()
    router = DCIRouter(spells_root=tmp_path)
    assert await router._search_grimoires("my grimoire") == [tmp_path / "my_grimoire"]
    assert await router._search_grimoires("") == []
