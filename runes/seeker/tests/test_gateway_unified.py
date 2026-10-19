from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_provider.registry import RealmRegistry
from mvgeos_runes.loader import load_factory_from_manifest
from mvgeos_runes.manifest import load_manifest
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes_seeker.activate_spell import ActivateSpell
from mvgeos_runes_seeker.connector import MCPConnector
from mvgeos_runes_seeker.discovery import MCPConfigDiscovery
from mvgeos_runes_seeker.ledger import SeekerLedger
from mvgeos_runes_seeker.skill_execute import SkillExecuteSpell
from mvgeos_runes_seeker.unload_spell import UnloadSpell


@pytest.fixture
def seeker_loaded() -> tuple[RuneRunner, Any, Any]:
    rune_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    runner = RuneRunner()
    diags: list[Any] = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diags)
    assert factory is not None
    return runner, factory, manifest


@pytest.mark.asyncio
async def test_search_first_gate_enforced(tmp_path: Path) -> None:
    ledger = SeekerLedger()
    runner = RuneRunner()
    api = runner.create_api(rune_name="seeker")

    discovery = MCPConfigDiscovery(search_roots=[tmp_path])
    connections: dict[str, Any] = {}

    activate = ActivateSpell(
        rune_api=api,
        ledger=ledger,
        spell_registry=MagicMock(),
        discovery=discovery,
        connections=connections,
        skill_dirs=[tmp_path],
    )

    # Attempting to activate a spell that was never searched
    res = await activate.execute("call-1", {"spells": ["bash"]})
    assert res["status"] == "error"
    assert "has not been returned by 'search'" in res["error"]

    # Now simulate searching and finding bash
    ledger.discovered_spells.add("bash")
    runner.set_global_spell_allowlist(["search", "activate"])
    res2 = await activate.execute("call-2", {"spells": ["bash"]})
    assert res2["status"] == "ok"
    allowlist = runner.get_global_spell_allowlist()
    assert allowlist is not None
    assert "bash" in allowlist


@pytest.mark.asyncio
async def test_execute_requires_prior_activation(tmp_path: Path) -> None:
    ledger = SeekerLedger()

    execute_spell = SkillExecuteSpell(
        provider_registry=RealmRegistry(),
        skill_dirs=[tmp_path],
        ledger=ledger,
    )

    # Calling execute on an un-activated skill
    res = await execute_spell.execute(
        "call-1", {"skill_name": "git-workflow", "script": "run.py"}
    )
    assert res["status"] == "error"
    assert "must be activated via 'activate'" in res["error"]


@pytest.mark.asyncio
async def test_unload_narrows_allowlist_and_closes_mcp(tmp_path: Path) -> None:
    ledger = SeekerLedger()
    runner = RuneRunner()
    api = runner.create_api(rune_name="seeker")

    mock_conn = AsyncMock()
    connections: dict[str, Any] = {"mock_server": mock_conn}

    runner.set_global_spell_allowlist(
        ["search", "activate", "bash", "mcp_mock_server_tool"]
    )
    ledger.discovered_spells.update(["bash", "mcp_mock_server_tool"])
    ledger.activated_skills.add("git-workflow")

    unload_spell = UnloadSpell(
        rune_api=api,
        ledger=ledger,
        connections=cast(dict[str, MCPConnector], connections),
    )

    res = await unload_spell.execute(
        "call-1",
        {
            "spells": ["bash"],
            "skills": ["git-workflow"],
            "mcp_servers": ["mock_server"],
        },
    )
    assert res["status"] == "ok"
    allowlist = runner.get_global_spell_allowlist()
    assert allowlist is not None
    assert "bash" not in allowlist
    assert "mcp_mock_server_tool" not in allowlist
    assert "git-workflow" not in ledger.activated_skills
    assert mock_conn.close.await_count == 1
