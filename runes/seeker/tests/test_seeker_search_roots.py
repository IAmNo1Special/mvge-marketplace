"""Seeker must search the layers the engine actually writes.

Four search roots were spelled out inside the Rune as relative literals
under ``.agents/.mvgeos/``. That layer is dead: ADR-0002 chose it,
ADR-0014 superseded it, and nothing in either repository has ever written
there. Each of those searches therefore matched nothing on a correctly
installed system -- and matched nothing *silently*, because an empty
result is indistinguishable from a layer the Summoner never installed.

Every fixture here relocates the global layer into ``tmp_path``. Nothing in
this file may read the Summoner's real ``~/.agents``: a search proved
against the real layer proves nothing about a relocated one.

``~/.claude/skills`` is deliberately left home-relative. It is a
third-party convention rather than our layer, and relocating our own
directory does not relocate someone else's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge.core import (
    DEFAULT_AGENT_NAME,
    GLOBAL_DIR_ENV,
    agent_spells_dir,
    extensions_dir,
    skills_dir,
)
from mvge.provider.registry import RealmRegistry
from mvgeos_runes_seeker.dci_matcher import DCISkillMatcher
from mvgeos_runes_seeker.discovery import MCPConfigDiscovery
from mvgeos_runes_seeker.mcp_spell import MCPSearchSpell
from mvgeos_runes_seeker.router import DCIRouter, SpellSearchError
from mvgeos_runes_seeker.skill_execute import SkillExecuteSpell
from mvgeos_runes_seeker.skill_spell import SkillSearchSpell
from mvgeos_runes_seeker.spell import ToolSearchSpell

AGENT_NAME = "probe-mvge"


@pytest.fixture
def relocated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``.agents`` layer at a temp root for one test."""
    # Deliberately local rather than shared from the repository root:
    # each rune is an independently installable, independently testable
    # package (its own pyproject.toml declares its own testpaths), and a
    # suite run from inside the rune directory never loads the root
    # conftest.py.
    root = tmp_path / "global"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(root))
    return root


def seed_spell(agent_name: str, name: str) -> Path:
    """Write one discoverable Spell file into the agent-scope layer.

    The body repeats ``name`` with underscores turned into spaces because
    that is both what rg has to match and what the stem fast path needs:
    ``tool_search`` searches file contents, then confirms the single hit
    normalises into the filename.
    """
    spells = agent_spells_dir(agent_name) / "grimoire"
    spells.mkdir(parents=True, exist_ok=True)
    path = spells / f"{name}.py"
    spoken = name.replace("_", " ")
    path.write_text(f'"""{spoken}."""\n', encoding="utf-8")
    return path


def seed_skill(name: str) -> Path:
    """Write one discoverable Skill, with a script, into the user layer."""
    skill = skills_dir() / name
    (skill / "scripts").mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"# {name}\n\nA seeded skill for the relocation probe.\n", encoding="utf-8"
    )
    (skill / "scripts" / "helper.py").write_text("print('seeded')\n", encoding="utf-8")
    return skill


def seed_mcp_config(name: str) -> Path:
    """Write one ``*.mcp.json`` into the user-scope Rune layer."""
    extensions_dir().mkdir(parents=True, exist_ok=True)
    path = extensions_dir() / f"{name}.mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {
                        "command": "true",
                        "transport": "stdio",
                        "description": f"the {name} server",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def tool_search(agent_name: str | None = AGENT_NAME) -> ToolSearchSpell:
    return ToolSearchSpell(
        provider_registry=RealmRegistry(),
        agent_name=agent_name,
        nlt_api_key="",
    )


def skill_search() -> SkillSearchSpell:
    return SkillSearchSpell(provider_registry=RealmRegistry(), nlt_api_key="")


def mcp_search() -> MCPSearchSpell:
    return MCPSearchSpell(provider_registry=RealmRegistry())


# --------------------------------------------------------------------------
# tool_search: the agent-scope spells layer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_search_finds_a_spell_in_the_agents_layer(
    relocated_global: Path,
) -> None:
    """A Spell installed where the engine installs Spells is discoverable.

    This is the behaviour the dead relative path broke. The single-match
    stem fast path is what keeps the assertion hermetic: no selection stage,
    so no Realm and no network.
    """
    seed_spell(AGENT_NAME, "relocated_probe")

    result = await tool_search().execute("cast", {"operation": "relocated probe"})

    assert result["error"] is None
    assert [r["name"] for r in result["results"]] == ["relocated_probe"]


@pytest.mark.asyncio
async def test_tool_search_root_is_resolved_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer is read when the Spell runs, not when the Rune loads.

    ``rune_factory`` runs at Rune-load time, which can be well after the
    process started and after a harness relocated the layer. Freezing the
    root at construction is what would make a late-set override invisible.
    """
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)
    spell = tool_search()

    relocated = tmp_path / "late-relocated"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(relocated))
    seed_spell(AGENT_NAME, "late_probe")

    result = await spell.execute("cast", {"operation": "late probe"})

    assert [r["name"] for r in result["results"]] == ["late_probe"]


def test_tool_search_root_agrees_with_the_agents_directory(
    relocated_global: Path,
) -> None:
    """Route through the engine's Spells resolver rather than re-deriving it.

    Asserting the engine's own answer is what makes this a contract on
    ``mvge.core.agent_spells_dir``. A hardcoded literal, or a local
    composition from ``agent_dir``, would let the two drift apart again --
    which is the defect this file exists to catch. It did: ``paths`` composed
    ``agent_dir(name) / "spells"`` while the engine exported the directory it
    searches, and the two only agreed because they agreed by coincidence.
    """
    seed_spell(DEFAULT_AGENT_NAME, "default_probe")

    assert tool_search(agent_name=None).spells_root == agent_spells_dir(
        DEFAULT_AGENT_NAME
    )


def test_tool_search_root_is_not_the_dead_layer(
    relocated_global: Path,
) -> None:
    """The dead ``.mvgeos`` layout is gone from the search root.

    The failure this pins is silent: the path is well-formed, so a
    regression shows up only as a Rune that finds nothing.
    """
    assert ".mvgeos" not in tool_search().spells_root.parts


# --------------------------------------------------------------------------
# skill_search / skill_execute: the user-scope skills layer
# --------------------------------------------------------------------------


def test_skill_search_discovers_the_relocated_skills_layer(
    relocated_global: Path,
) -> None:
    """The user-scope skills directory is searched where the engine writes it."""
    seed_skill("relocated-skill")

    assert skills_dir().resolve() in DCISkillMatcher().discover_skill_dirs()


def test_skill_search_root_is_not_the_dead_layer(
    relocated_global: Path,
) -> None:
    """The dead ``.mvgeos`` layout is gone from the skill roots."""
    roots = DCISkillMatcher().skill_dirs

    assert roots, "no skill root resolved at all"
    assert all(".mvgeos" not in root.parts for root in roots)


@pytest.mark.asyncio
async def test_skill_execute_finds_a_script_in_the_relocated_skills_layer(
    relocated_global: Path,
) -> None:
    """End to end: a discovered Skill's script runs out of the relocated layer.

    ``skill_execute`` shares one root resolver with ``skill_search``, so
    this is the observable proof that the resolver followed the layer --
    no Realm and no network involved.
    """
    seed_skill("relocated-skill")

    result = await SkillExecuteSpell(
        provider_registry=RealmRegistry(),
        agent_name=AGENT_NAME,
        nlt_api_key="",
    ).execute("cast", {"skill_name": "relocated-skill", "script": "helper.py"})

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "seeded"


def test_claude_skills_convention_is_still_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third-party convention is not relocated along with our layer.

    Locked deliberately: relocating ``~/.claude`` would be wrong, and this
    is the assertion that stops someone "fixing" it alongside our own path.
    """
    fake_home = tmp_path / "fakehome"
    (fake_home / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    discovered = DCISkillMatcher().discover_skill_dirs()

    assert (fake_home / ".claude" / "skills").resolve() in discovered


# --------------------------------------------------------------------------
# mcp_search: the user-scope Rune layer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_search_finds_a_server_in_the_relocated_layer(
    relocated_global: Path,
) -> None:
    """An ``*.mcp.json`` installed where the engine installs Runes is found."""
    seed_mcp_config("relocated_server")

    found = await MCPConfigDiscovery().search("relocated_server")

    assert [info.name for info in found] == ["relocated_server"]


def test_mcp_search_root_agrees_with_the_engines_extensions_layer(
    relocated_global: Path,
) -> None:
    """The default root is ``extensions_dir()``, not an invented directory."""
    extensions_dir().mkdir(parents=True)

    assert MCPConfigDiscovery().search_roots == [extensions_dir()]


def test_mcp_search_root_is_not_the_dead_layer(
    relocated_global: Path,
) -> None:
    """The dead ``.mvgeos`` layout is gone from the MCP search roots."""
    discovery = MCPConfigDiscovery()

    assert discovery.search_roots, "no MCP search root resolved at all"
    assert all(".mvgeos" not in root.parts for root in discovery.search_roots)


# --------------------------------------------------------------------------
# A missing root is reported, never silently empty
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_search_reports_a_missing_spells_root(
    relocated_global: Path,
) -> None:
    """No Spells directory at all is a diagnosis, not an empty result."""
    result = await tool_search().execute("cast", {"operation": "anything"})

    assert result["spells_found"] == 0
    assert str(agent_spells_dir(AGENT_NAME)) in result["error"]


@pytest.mark.asyncio
async def test_skill_search_reports_a_missing_skills_root(
    relocated_global: Path,
) -> None:
    """No Skills directory at all is a diagnosis, not an empty result."""
    result = await skill_search().execute("cast", {"task": "anything"})

    assert result["skills_found"] == 0
    assert str(skills_dir()) in result["error"]


@pytest.mark.asyncio
async def test_mcp_search_reports_a_missing_extensions_root(
    relocated_global: Path,
) -> None:
    """No Rune layer at all is a diagnosis, not an empty result."""
    result = await mcp_search().execute("cast", {"capability": "anything"})

    assert result["serversFound"] == 0
    assert str(extensions_dir()) in result["error"]


@pytest.mark.asyncio
async def test_an_existing_but_empty_layer_is_a_real_zero(
    relocated_global: Path,
) -> None:
    """The distinction the three tests above force.

    A missing root is a misconfiguration and says so; a root that exists
    and holds nothing is a genuine answer. ``skill_search`` is absent here
    because its selection stage needs a registered Realm -- its root
    resolution is pinned by the missing-root test above.
    """
    agent_spells_dir(AGENT_NAME).mkdir(parents=True)
    skills_dir().mkdir(parents=True)
    extensions_dir().mkdir(parents=True)

    tool_result = await tool_search().execute("cast", {"operation": "anything"})
    mcp_result = await mcp_search().execute("cast", {"capability": "anything"})

    assert tool_result == {"spells_found": 0, "results": [], "error": None}
    assert mcp_result == {"serversFound": 0, "servers": [], "error": None}


@pytest.mark.asyncio
async def test_the_router_names_a_missing_root_instead_of_blaming_the_regex(
    tmp_path: Path,
) -> None:
    """rg cannot tell a missing path from a bad pattern, so the router must.

    ``rg`` exits 2 on both. ``_run_rg`` reads exit 2 as
    ``RG_EXIT_BAD_REGEX`` and reports it as such, so a root that was never
    there produced "rg error (exit code 2)" and pointed the Summoner at
    their own query. ``tool_search`` guards its root before building the
    router, which hid this until the router was reached directly.
    """
    missing = tmp_path / "no-such-spells"

    router = DCIRouter(spells_root=missing)
    with pytest.raises(SpellSearchError) as caught:
        await router.route({"operation": "cast"})

    message = str(caught.value)
    assert str(missing) in message
    assert "exit code 2" not in message
