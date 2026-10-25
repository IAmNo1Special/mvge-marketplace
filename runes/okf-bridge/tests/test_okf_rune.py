"""Unit and integration tests for okf-bridge rune lifecycle and spells."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mvge.runes.types import BeforeMvgeStartData, SigilHook, SpellDefinition
from mvge_runes_okf_bridge.rune import rune_factory


class MockRuneAPI:
    def __init__(self, cwd: Path) -> None:
        self._runner = MagicMock()
        self._runner.context = MagicMock()
        self._runner.context.cwd = str(cwd)
        self.hooks: dict[SigilHook, list] = {}
        self.spells: dict[str, SpellDefinition] = {}
        self.commands: dict[str, dict] = {}

    def on(self, hook: SigilHook, handler) -> None:
        self.hooks.setdefault(hook, []).append(handler)

    def register_spell(self, spell: SpellDefinition) -> None:
        self.spells[spell.name] = spell

    def register_command(self, name: str, description: str, handler) -> None:
        self.commands[name] = {"description": description, "handler": handler}


@pytest.fixture()
def isolated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the global knowledge layer out of the real home dir."""
    fake = tmp_path / "fake-global"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(fake))
    return fake


@pytest.fixture()
def knowledge_dir(tmp_path: Path) -> Path:
    kd = tmp_path / ".agents" / "knowledge"
    kd.mkdir(parents=True)
    (kd / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8"
    )
    (kd / "service-a.md").write_text(
        "---\n"
        "type: Service\n"
        "title: Service Alpha\n"
        "description: Primary service\n"
        "tags: [api]\n"
        "status: stable\n"
        "context: auto\n"
        "generated:\n"
        "  by: human:alice\n"
        "  at: '2026-09-01'\n"
        "---\n"
        "# Service Alpha\nBody content.\n",
        encoding="utf-8",
    )
    return kd


@pytest.mark.asyncio
async def test_okf_rune_lifecycle_and_spells(
    tmp_path: Path, isolated_global: Path, knowledge_dir: Path
) -> None:
    api = MockRuneAPI(cwd=tmp_path)
    rune_factory(api)

    # Registered hooks
    assert SigilHook.SESSION_START in api.hooks
    assert SigilHook.BEFORE_MVGE_START in api.hooks
    assert SigilHook.CONTEXT_TRANSFORM in api.hooks
    assert SigilHook.SESSION_SHUTDOWN in api.hooks

    # Registered spells use the concept vocabulary
    assert "concept_search" in api.spells
    assert "concept_get" in api.spells
    assert "concept_validate" in api.spells
    assert "concept_write" in api.spells
    assert "concept_verify" in api.spells
    assert "concept_deprecate" in api.spells
    assert "concept_set_context" in api.spells
    assert "okf_search" not in api.spells

    assert "okf" in api.commands

    # 1. before_mvge_start injects budgeted working concepts
    before_hook = api.hooks[SigilHook.BEFORE_MVGE_START][0]

    def fresh_data() -> BeforeMvgeStartData:
        return BeforeMvgeStartData(
            base_prompt="System Prompt:",
            spell_names=[],
            config_dir=str(tmp_path),
            custom_prompt="",
            agent_name="test",
            cwd=str(tmp_path),
        )

    data = fresh_data()
    res_data = await before_hook(data)
    assert "<working_concepts" in res_data.base_prompt
    assert "service-a" in res_data.base_prompt
    assert "<knowledge_catalog" not in res_data.base_prompt

    # 2. concept_search
    search_handler = api.spells["concept_search"]._handler
    search_json = await search_handler(arguments={"query": "Alpha"})
    search_results = json.loads(search_json)
    assert len(search_results) == 1
    assert search_results[0]["id"] == "service-a"

    # 3. concept_get
    get_handler = api.spells["concept_get"]._handler
    get_json = await get_handler(arguments={"concept_id": "service-a"})
    concept_data = json.loads(get_json)
    assert concept_data["id"] == "service-a"
    assert concept_data["type"] == "Service"
    assert concept_data["title"] == "Service Alpha"

    missing_json = await get_handler(arguments={"concept_id": "nonexistent"})
    assert "not found" in missing_json

    # 4. concept_validate (merged layers)
    val_handler = api.spells["concept_validate"]._handler
    val_json = await val_handler(arguments={})
    val_data = json.loads(val_json)
    assert val_data["valid"] is True
    assert val_data["concepts_checked"] == 1

    # 5. concept_write -> concept_verify -> concept_deprecate lifecycle
    write_handler = api.spells["concept_write"]._handler
    write_json = await write_handler(
        arguments={
            "concept_id": "notes/idea",
            "type": "Note",
            "title": "An Idea",
            "description": "Short desc.",
            "body": "Longer body.",
            "context": "auto",
        }
    )
    write_data = json.loads(write_json)
    assert write_data["id"] == "notes/idea"
    assert write_data["trust_tier"] == "unverified"
    assert (knowledge_dir / "notes" / "idea.md").is_file()

    # The new concept is searchable and injected without a reload
    search_json2 = await search_handler(arguments={"query": "idea"})
    assert any(r["id"] == "notes/idea" for r in json.loads(search_json2))
    res_data2 = await before_hook(fresh_data())
    assert "notes/idea" in res_data2.base_prompt

    verify_handler = api.spells["concept_verify"]._handler
    verify_json = await verify_handler(
        arguments={"concept_id": "notes/idea", "by": "human:malcom"}
    )
    verify_data = json.loads(verify_json)
    assert verify_data["trust_tier"] == "human-reviewed"

    set_ctx_handler = api.spells["concept_set_context"]._handler
    ctx_json = await set_ctx_handler(
        arguments={"concept_id": "notes/idea", "context": "search-only"}
    )
    assert json.loads(ctx_json)["context"] == "search-only"
    res_data3 = await before_hook(fresh_data())
    assert "notes/idea" not in res_data3.base_prompt

    deprecate_handler = api.spells["concept_deprecate"]._handler
    dep_json = await deprecate_handler(arguments={"concept_id": "service-a"})
    assert json.loads(dep_json)["status"] == "deprecated"
    res_data4 = await before_hook(fresh_data())
    assert "service-a" not in res_data4.base_prompt

    # 6. /okf command
    cmd_handler = api.commands["okf"]["handler"]
    status_out = await cmd_handler("status")
    assert "OKF Knowledge Bundle Status" in status_out
    assert "Total Concepts:  2" in status_out
    assert "Layers:" in status_out

    search_out = await cmd_handler("search Alpha")
    assert "FOUND: 1 concepts" in search_out

    val_out = await cmd_handler("validate")
    assert "OK: Bundle is conformant" in val_out

    graph_out = await cmd_handler("graph")
    assert "OK: Interactive graph rendered" in graph_out
    assert (knowledge_dir / "viz.html").is_file()

    unknown_out = await cmd_handler("unknown_action")
    assert "Unknown okf command" in unknown_out

    # 7. context transform replaces, never accumulates.
    # (By now nothing is auto-injectable: service-a is deprecated and
    # notes/idea is search-only, so the block is empty and silent.)
    transform_hook = api.hooks[SigilHook.CONTEXT_TRANSFORM][0]
    inv_list = [{"role": "user", "content": "Hello"}]
    transformed = await transform_hook(inv_list)
    assert len(transformed) == 1
    assert transformed[0]["content"] == "Hello"

    assert await transform_hook("not-a-list") == "not-a-list"

    # 8. session shutdown clears state
    await api.hooks[SigilHook.SESSION_SHUTDOWN][0]()


@pytest.mark.asyncio
async def test_context_transform_appends_then_replaces(
    tmp_path: Path, isolated_global: Path, knowledge_dir: Path
) -> None:
    api = MockRuneAPI(cwd=tmp_path)
    rune_factory(api)
    transform = api.hooks[SigilHook.CONTEXT_TRANSFORM][0]

    invs = [{"role": "user", "content": "Hello"}]
    once = await transform(invs)
    assert "<working_concepts" in once[0]["content"]
    assert "service-a" in once[0]["content"]

    twice = await transform(once)
    assert twice[0]["content"].count("<working_concepts") == 1
    assert "service-a" in twice[0]["content"]
