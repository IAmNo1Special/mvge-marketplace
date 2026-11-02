from __future__ import annotations

from typing import Any

from mvge.core import extensions_dir
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from mvge_runes_seeker.activate_spell import ActivateSpell
from mvge_runes_seeker.discovery import MCPConfigDiscovery
from mvge_runes_seeker.ledger import SeekerLedger
from mvge_runes_seeker.mcp_spell import MCPSearchSpell
from mvge_runes_seeker.skill_execute import SkillExecuteSpell
from mvge_runes_seeker.skill_spell import SkillSearchSpell
from mvge_runes_seeker.spell import SearchSpell, ToolSearchSpell
from mvge_runes_seeker.unload_spell import UnloadSpell


def _sigil_field(data: Any, name: str) -> Any:
    """Read a field from typed sigil data or a raw dict."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get(name)
    return getattr(data, name, None)


def rune_factory(api: RuneAPI) -> None:
    # Phase 2A: seeker model calls go through the engine-held
    # capability (api.stream_model_text). No provider key, realm
    # registry, or runner object ever reaches seeker code.
    agent_name = getattr(api.context, "agent_name", "coding-agent") or "coding-agent"
    stream_text = api.stream_model_text

    ledger = SeekerLedger()
    connections: dict[str, Any] = {}
    mcp_roots = [extensions_dir()]
    mcp_discovery = MCPConfigDiscovery(search_roots=mcp_roots)

    # 1. Unified 4 Meta-Spells
    search_spell = SearchSpell(
        stream_text=stream_text,
        agent_name=agent_name,
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
        stream_text=stream_text,
        agent_name=agent_name,
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
        stream_text=stream_text,
        agent_name=agent_name,
        rune_api=api,
        ledger=ledger,
    )

    skill_search = SkillSearchSpell(
        stream_text=stream_text,
        agent_name=agent_name,
    )

    legacy_skill_execute = SkillExecuteSpell(
        stream_text=stream_text,
        agent_name=agent_name,
        ledger=ledger,
        name="skill_execute",
    )

    mcp_search = MCPSearchSpell(
        stream_text=stream_text,
        rune_api=api,
        config={
            "search_roots": mcp_roots,
            "nlt_model": "openrouter/free",
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
    async def close_mcp_connections(data: Any) -> None:
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
    async def on_after_spell_result(data: Any) -> None:
        spell_name = _sigil_field(data, "spell_name")
        if spell_name:
            ledger.last_active_turn[spell_name] = ledger.current_turn

    api.on(SigilHook.AFTER_SPELL_RESULT, on_after_spell_result)

    # Idle TTL Eviction on TURN_END
    async def on_turn_end(data: Any) -> None:
        ledger.current_turn += 1
        idle_turn_ttl = 8
        allowlist = api.get_global_spell_allowlist()
        if allowlist is None:
            return

        gateway_spells = set(api.gateway_spell_names())
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

    def activate_seeker_meta_tools(_data: Any) -> None:
        api.set_active_spells(meta_spells)

    api.on(SigilHook.SESSION_START, activate_seeker_meta_tools)
