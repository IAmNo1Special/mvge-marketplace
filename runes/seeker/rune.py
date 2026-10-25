from __future__ import annotations

from typing import Any

from mvge.core import extensions_dir
from mvge.provider.registry import RealmRegistry
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from mvgeos_runes_seeker.activate_spell import ActivateSpell
from mvgeos_runes_seeker.discovery import MCPConfigDiscovery
from mvgeos_runes_seeker.ledger import SeekerLedger
from mvgeos_runes_seeker.mcp_spell import MCPSearchSpell
from mvgeos_runes_seeker.skill_execute import SkillExecuteSpell
from mvgeos_runes_seeker.skill_spell import SkillSearchSpell
from mvgeos_runes_seeker.spell import SearchSpell, ToolSearchSpell
from mvgeos_runes_seeker.unload_spell import UnloadSpell


def rune_factory(api: RuneAPI) -> None:
    provider_registry = RealmRegistry()
    runner = api._runner
    agent_name = getattr(runner.context, "agent_name", "coding-agent")
    api_key = getattr(runner.context, "api_key", "")

    ledger = SeekerLedger()
    connections: dict[str, Any] = {}
    mcp_roots = [extensions_dir()]
    mcp_discovery = MCPConfigDiscovery(search_roots=mcp_roots)

    # 1. Unified 4 Meta-Spells
    search_spell = SearchSpell(
        provider_registry=provider_registry,
        agent_name=agent_name,
        nlt_api_key=api_key,
        rune_api=api,
        ledger=ledger,
        mcp_roots=mcp_roots,
    )

    activate_spell = ActivateSpell(
        rune_api=api,
        ledger=ledger,
        spell_registry=search_spell.spells_root,
        discovery=mcp_discovery,
        connections=connections,
    )

    execute_spell = SkillExecuteSpell(
        provider_registry=provider_registry,
        agent_name=agent_name,
        nlt_api_key=api_key,
        ledger=ledger,
        name="execute",
    )

    unload_spell = UnloadSpell(
        rune_api=api,
        ledger=ledger,
        connections=connections,
    )

    # 2. Legacy Spells for backward compatibility
    tool_search = ToolSearchSpell(
        provider_registry=provider_registry,
        agent_name=agent_name,
        nlt_api_key=api_key,
        rune_api=api,
        ledger=ledger,
    )

    skill_search = SkillSearchSpell(
        provider_registry=provider_registry,
        agent_name=agent_name,
        nlt_api_key=api_key,
    )

    legacy_skill_execute = SkillExecuteSpell(
        provider_registry=provider_registry,
        agent_name=agent_name,
        nlt_api_key=api_key,
        ledger=ledger,
        name="skill_execute",
    )

    mcp_search = MCPSearchSpell(
        provider_registry=provider_registry,
        rune_runner=runner,
        rune_api=api,
        config={
            "search_roots": mcp_roots,
            "nlt_model": "openrouter/free",
            "nlt_api_key": api_key,
            "timeout": {
                "init": 15,
                "stdio": 30,
                "http": 30,
                "tool_list": 60,
            },
        },
    )

    # Register all spells on api
    api.register_spell(search_spell)
    api.register_spell(activate_spell)
    api.register_spell(execute_spell)
    api.register_spell(unload_spell)

    api.register_spell(tool_search)
    api.register_spell(skill_search)
    api.register_spell(legacy_skill_execute)
    api.register_spell(mcp_search)

    # Clean up connections on session shutdown
    async def close_mcp_connections(data: dict[str, Any]) -> None:
        for conn in list(connections.values()):
            if hasattr(conn, "close"):
                await conn.close()
        connections.clear()
        if hasattr(mcp_search, "_connections"):
            for conn in list(mcp_search._connections.values()):
                await conn.close()
            mcp_search._connections.clear()

    api.on(SigilHook.SESSION_SHUTDOWN, close_mcp_connections)

    # Track last active turn of used spells
    async def on_after_spell_result(data: dict[str, Any]) -> None:
        spell_name = data.get("spell_name")
        if spell_name:
            ledger.last_active_turn[spell_name] = ledger.current_turn

    api.on(SigilHook.AFTER_SPELL_RESULT, on_after_spell_result)

    # Idle TTL Eviction on TURN_END
    async def on_turn_end(data: dict[str, Any]) -> None:
        ledger.current_turn += 1
        idle_turn_ttl = 8
        allowlist = api.get_global_spell_allowlist()
        if allowlist is None:
            return

        gateway_spells = set(runner.gateway_spell_names())
        evictable: list[str] = []
        for name in allowlist:
            if name in gateway_spells:
                continue
            last_turn = ledger.last_active_turn.get(name, 0)
            if ledger.current_turn - last_turn >= idle_turn_ttl:
                evictable.append(name)

        if evictable:
            api.narrow_global_allowlist(evictable)
            # If all tools of a connected MCP server are evicted, close it
            for s_name in list(connections.keys()):
                server_prefix = f"mcp_{s_name}_"
                remaining_tools = [
                    s
                    for s in (api.get_global_spell_allowlist() or [])
                    if s.startswith(server_prefix)
                ]
                if not remaining_tools:
                    conn = connections.pop(s_name, None)
                    if conn is not None and hasattr(conn, "close"):
                        await conn.close()

    api.on(SigilHook.TURN_END, on_turn_end)

    # Initial meta spells surfaced to the model
    meta_spells = [
        "search",
        "activate",
        "execute",
        "unload",
        "tool_search",
        "skill_search",
        "skill_execute",
        "mcp_search",
    ]

    def activate_seeker_meta_tools(_data: dict[str, Any]) -> None:
        api.set_active_spells(meta_spells)

    api.on(SigilHook.SESSION_START, activate_seeker_meta_tools)
