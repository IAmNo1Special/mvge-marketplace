from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from coding_mvge import root_mvge
from coding_mvge.spells import (
    bash,
    grep,
    read,
    write,
)
from mvge.agent import (
    FunctionSpell,
    Mvge,
)
from mvge.agent.environment import MvgeEnvironment
from mvge.agent.harness._state import MvgeState
from mvge.core.channel import (
    MvgeResponse,
    StopReason,
)
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.events import ContemplationLevel
from mvge.runes.types import SpellDefinition


@pytest.fixture
def agent() -> Mvge:
    return Mvge(api_key="test-key")


@pytest.fixture
def _register_mock_openrouter_realm() -> Any:
    from mvge.provider.registry import get_default_realm_registry

    reg = get_default_realm_registry()
    reg.register_realm_factory("openrouter", lambda **kw: _MockRealm())
    yield
    reg.unregister_realm_factory("openrouter")


class _MockRealm:
    def __init__(self) -> None:
        self.close: Any = AsyncMock()

    def stream(self, *args: object, **kwargs: object) -> object:
        return _Iter()


def _make_mock_realm(text: str = "Hello") -> _MockRealm:
    return _MockRealm()


def _install_mock(agent: Mvge, text: str = "Hello") -> _MockRealm:
    from mvge.core.channel import Model

    mock_realm = _make_mock_realm(text)
    agent._realm = mock_realm  # type: ignore[assignment]
    agent._model = Model(
        id="test-model",
        name="Test",
        realm="test",
        base_url="",
        api_key="test-key",
    )
    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.shutdown = AsyncMock()
    mock_session.active_leaf_id_async = AsyncMock(return_value=None)
    mock_session.record_message_async = AsyncMock(return_value=None)
    agent._agent_tome = mock_session
    agent._state = MvgeState(
        system_prompt="test",
        model={"id": "test-model", "name": "Test"},
        contemplation_level=ContemplationLevel.OFF,
        spells=[],
        invocations=[],
        rune_runner=None,
        agent_tome=agent._agent_tome,
    )
    from mvge.agent.harness import MvgeHarness

    agent._harness = MvgeHarness(
        state=agent._state,
        tome=agent._agent_tome,
        realm=mock_realm,
        model=agent._model,
    )
    agent._initialized = True
    return mock_realm


class _Iter:
    def __aiter__(self) -> object:
        from mvge.core.channel import (
            Model,
            RealmResponse,
        )

        model = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )

        async def gen() -> object:
            yield RealmResponse(
                model=model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            )

        return gen()


class TestMvgeInit:
    def test_stores_config(self) -> None:
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            overrides={
                "model": "test-model",
                "temperature": 0.5,
                "max_tokens": 2048,
                "contemplation_level": "high",
            },
            custom_prompt="Custom prompt",
        )
        agent = Mvge(
            api_key="k",
            spells=[bash, read],
            custom_system_prompt="Custom prompt",
            environment=env,
        )
        assert agent._api_key == "k"
        assert agent._model_id == "test-model"
        assert len(agent._spells) == 2
        assert agent._custom_system_prompt == "Custom prompt"
        assert agent._temperature == 0.5
        assert agent._max_tokens == 2048
        assert agent._contemplation_level == "high"

    def test_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        agent = Mvge(api_key="k", model_id="openrouter/free")
        assert agent._model_id == "openrouter/free"
        assert agent._spells == []
        assert agent._custom_system_prompt == ""
        assert agent._temperature == 0.7
        assert agent._max_tokens == 4096
        assert agent._contemplation_level == "medium"

    def test_coding_mvge_instance(self) -> None:
        assert root_mvge.name == "coding_mvge"
        assert len(root_mvge.enabled_spells) == 9
        assert set(root_mvge.enabled_spells) == {
            "bash",
            "read",
            "write",
            "edit",
            "find",
            "list_files",
            "grep",
            "search_web",
            "read_url",
        }

    def test_default_tome_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The root conftest isolates MVGE_GLOBAL_DIR for every test; this
        # test asserts the default resolution, so it opts out explicitly.
        monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)
        agent = Mvge(api_key="k")
        assert agent._tome_dir == Path.home() / ".agents" / "sessions"
        assert agent.tome_dir == Path.home() / ".agents" / "sessions"


class TestMvgeBuildSpells:
    def test_build_spells_returns_coerced_spells(self) -> None:
        agent = Mvge(api_key="k", spells=[bash, read, write])
        spells = agent._build_spells()
        assert len(spells) == 3
        assert [s.name for s in spells] == ["bash", "read", "write"]
        assert all(isinstance(s, FunctionSpell) for s in spells)

    def test_build_spells_with_raw_functions(self) -> None:
        def custom_tool(arg: str) -> str:
            """A test tool."""
            return f"result: {arg}"

        agent = Mvge(api_key="k", spells=[custom_tool])
        spells = agent._build_spells()
        assert len(spells) == 1
        assert spells[0].name == "custom_tool"
        assert spells[0].description == "A test tool."

    def test_build_spells_with_rune_runner(self, agent: Mvge) -> None:
        mock_runner = MagicMock()
        # MagicMock supports __iter__ and yields nothing, so an
        # unconfigured return here looks like an empty allowlist and
        # silently filters every spell out. None is the engine's
        # 'no global filtering' value.
        mock_runner.get_global_spell_allowlist.return_value = None
        mock_runner.get_all_registered_spells.return_value = [
            SpellDefinition(
                name="tool_search",
                description="Search for tools",
                parameters={},
            ),
            SpellDefinition(
                name="skill_search",
                description="Search for skills",
                parameters={},
            ),
            SpellDefinition(
                name="inactive_tool", description="Inactive", parameters={}
            ),
        ]
        mock_runner.get_active_spells.return_value = [
            "tool_search",
            "skill_search",
        ]
        agent._runner = mock_runner
        spells = agent._build_spells()
        names = {s.name for s in spells}
        assert "tool_search" in names
        assert "skill_search" in names
        assert "inactive_tool" not in names

    def test_render_prompt_lists_active_spells(self, agent: Mvge) -> None:
        from mvge.agent.environment import render_prompt as env_render_prompt

        mock_runner = MagicMock()
        # MagicMock supports __iter__ and yields nothing, so an
        # unconfigured return here looks like an empty allowlist and
        # silently filters every spell out. None is the engine's
        # 'no global filtering' value.
        mock_runner.get_global_spell_allowlist.return_value = None
        mock_runner.get_all_registered_spells.return_value = [
            SpellDefinition(name="tool_search", description="", parameters={}),
            SpellDefinition(name="skill_search", description="", parameters={}),
        ]
        mock_runner.get_active_spells.return_value = [
            "tool_search",
            "skill_search",
        ]
        agent._runner = mock_runner
        prompt = env_render_prompt(
            "You are Mvge", [s.name for s in agent._build_spells()], []
        )
        assert "Active spells:" in prompt
        assert "tool_search" in prompt
        assert "skill_search" in prompt


class TestMvgeProperties:
    def test_tome_id_none_before_init(self) -> None:
        agent = Mvge(api_key="k")
        assert agent.tome_id is None

    def test_enabled_spells(self) -> None:
        agent = Mvge(api_key="k", spells=[bash, grep])
        assert agent.enabled_spells == ["bash", "grep"]

    def test_registered_commands_empty(self) -> None:
        agent = Mvge(api_key="k")
        assert agent.registered_commands == []

    def test_registered_shortcuts_empty(self) -> None:
        agent = Mvge(api_key="k")
        assert agent.registered_shortcuts == []


class TestMvgeRun:
    @pytest.mark.asyncio
    async def test_initialize_then_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)

            result = await agent.run("Hi")
            assert result is not None
            await agent.close()

    @pytest.mark.asyncio
    async def test_raises_on_unknown_model(self) -> None:
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, overrides={"model": "unknown/model"}
        )
        agent = Mvge(
            api_key="test-key",
            spells=[],
            environment=env,
        )
        with pytest.raises(ValueError, match="Unknown model"):
            await agent.initialize()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)

            async with agent as a:
                result = await a.run("Hello from context manager")
                assert result is not None

    @pytest.mark.asyncio
    async def test_run_returns_mvge_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)

            result = await agent.run("What is it?")
            assert isinstance(result, MvgeResponse)
            assert result.content == [{"type": "text", "text": "Hello"}]
            await agent.close()

    @pytest.mark.asyncio
    async def test_initialize_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)
            await agent.initialize()
            assert agent._initialized
            await agent.initialize()
            assert agent._initialized
            await agent.close()

    @pytest.mark.asyncio
    async def test_run_with_spells(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[bash, read],
            )
            _install_mock(agent)

            result = await agent.run("list files")
            assert result is not None
            await agent.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
            )
            mock = _install_mock(agent)
            await agent.run("test")
            await agent.close()

            assert not agent._initialized
            mock.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)

            result1 = await agent.run("First prompt")
            assert result1 is not None

            result2 = await agent.run("Second prompt")
            assert result2 is not None

            assert agent.harness is not None
            assert len(agent.harness.state.invocations) >= 3
            await agent.close()

    @pytest.mark.asyncio
    async def test_multi_turn_state_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            _install_mock(agent)

            await agent.run("One")
            assert agent.harness is not None
            count_after_first = len(agent.harness.state.invocations)

            await agent.run("Two")
            count_after_second = len(agent.harness.state.invocations)

            assert count_after_second > count_after_first
            await agent.close()


@pytest.mark.usefixtures("_register_mock_openrouter_realm")
class TestMvgeSwitchModel:
    @pytest.mark.asyncio
    async def test_switch_model_preserves_session(self) -> None:
        from mvge.provider import list_models

        target = next(
            m.id
            for m in list_models()
            if m.id != "nvidia/nemotron-3-ultra-550b-a55b:free"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                model_id="openrouter/free",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            await agent.initialize()
            tome_before = agent.tome_id
            assert tome_before is not None

            await agent.switch_model(target)

            assert agent.tome_id == tome_before
            assert agent._model_id == target
            assert agent._model is not None
            assert agent._model.id == target
            assert agent.harness is not None
            assert agent.harness.snapshot.configured_model == target
            await agent.close()

    @pytest.mark.asyncio
    async def test_switch_model_unknown_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                model_id="openrouter/free",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            await agent.initialize()
            tome_before = agent.tome_id

            with pytest.raises(ValueError, match="Unknown model"):
                await agent.switch_model("unknown/model")

            assert agent.tome_id == tome_before
            assert agent._model_id == "openrouter/free"
            assert agent._model is not None
            assert agent._model.id == "openrouter/free"
            await agent.close()

    @pytest.mark.asyncio
    async def test_switch_model_same_model_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                model_id="openrouter/free",
                tome_dir=Path(tmpdir),
                spells=[],
            )
            await agent.initialize()
            await agent.switch_model("openrouter/free")
            assert agent._model_id == "openrouter/free"
            await agent.close()

    @pytest.mark.asyncio
    async def test_switch_model_before_init(self) -> None:
        from mvge.provider import list_models

        target = next(m.id for m in list_models() if m.id != "openrouter/free")
        agent = Mvge(api_key="test-key")
        await agent.switch_model(target)
        assert agent._model_id == target


class TestMvgeToolCalls:
    @pytest.mark.asyncio
    async def test_agent_executes_spell_and_continues_turn(self) -> None:
        from mvge.agent.function_spell import FunctionSpell
        from mvge.core.channel import RealmResponse, StopReason
        from mvge.core.spells import SpellResultMessage

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[bash],
            )
            _install_mock(agent)
            assert agent.harness is not None
            agent.harness.state.spells = [FunctionSpell(bash)]

            class FakeRealm:
                def __init__(self) -> None:
                    self.calls = 0

                async def close(self) -> None:
                    pass

                async def stream(
                    self,
                    model: object,
                    invocations: object,
                    config: object,
                    signal: object = None,
                ) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        yield RealmResponse(
                            model=model,  # type: ignore[arg-type]
                            invocation=MvgeResponse(
                                role="assistant",
                                content=[
                                    {
                                        "type": "spell_cast",
                                        "spell_cast": {
                                            "id": "call-1",
                                            "name": "bash",
                                            "arguments": {"command": "echo hi"},
                                        },
                                    }
                                ],
                                stop_reason=StopReason.SPELL_USE,
                            ),
                        )
                    else:
                        yield RealmResponse(
                            model=model,  # type: ignore[arg-type]
                            invocation=MvgeResponse(
                                role="assistant",
                                content=[{"type": "text", "text": "done"}],
                                stop_reason=StopReason.STOP,
                            ),
                        )

            agent._realm = FakeRealm()  # type: ignore[assignment]
            agent.harness.set_model_and_realm(agent._model, agent._realm)

            result = await agent.run("run echo")

            assert isinstance(result, MvgeResponse)
            assert result.content == [{"type": "text", "text": "done"}]
            assert agent.harness is not None
            assert any(
                isinstance(inv, SpellResultMessage)
                for inv in agent.harness.state.invocations
            )
            await agent.close()

    @pytest.mark.asyncio
    async def test_make_stream_sends_tools_schema(self) -> None:
        from mvge.agent.function_spell import FunctionSpell

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = Mvge(
                api_key="test-key",
                tome_dir=Path(tmpdir),
                spells=[bash, read],
            )
            _install_mock(agent)
            assert agent.harness is not None
            agent.harness.state.spells = [
                FunctionSpell(bash),
                FunctionSpell(read),
            ]

            captured: dict[str, Any] = {}

            class CaptureRealm:
                async def close(self) -> None:
                    pass

                async def stream(
                    self,
                    model: object,
                    invocations: object,
                    config: object,
                    signal: object = None,
                ) -> object:
                    captured["tools"] = config.tools  # type: ignore[attr-defined]
                    if len(captured["tools"]) < 0:
                        yield

            agent._realm = CaptureRealm()  # type: ignore[assignment]
            agent.harness.set_model_and_realm(agent._model, agent._realm)
            stream_fn = agent.harness._make_stream_fn()

            async for _ in stream_fn(agent.harness.state.invocations):
                pass

            assert captured.get("tools")
            names = [t["function"]["name"] for t in captured["tools"]]
            assert names == ["bash", "read"]
            props = captured["tools"][0]["function"]["parameters"]["properties"]
            assert "command" in props


class TestBuildSystemPrompt:
    def test_hardcoded_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = MvgeEnvironment.resolve(
                DEFAULT_AGENT_NAME,
                config_dir=Path(td) / "nonexistent",
                allow_unknown_agent=True,
            )
            prompt = MvgeEnvironment.render_prompt(
                env.resolved_prompt.text,
                spells=["bash", "read"],
            )
            assert "You are a Mvge" in prompt
            assert "Active spells:" in prompt
            assert "bash" in prompt
            assert "read" in prompt

    def test_config_dir_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = MvgeEnvironment.resolve(
                DEFAULT_AGENT_NAME,
                config_dir=Path(td) / "does-not-exist",
                allow_unknown_agent=True,
            )
            prompt = MvgeEnvironment.render_prompt(
                env.resolved_prompt.text,
                spells=["bash"],
            )
            assert "You are a Mvge" in prompt

    def test_custom_prompt_overrides_base(self) -> None:
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            custom_prompt="Custom agent prompt here.",
            allow_unknown_agent=True,
        )
        prompt = MvgeEnvironment.render_prompt(
            env.resolved_prompt.text,
            spells=env.spell_names or [],
        )
        assert "Custom agent prompt here." in prompt
        assert "You are Mvge" not in prompt

    def test_system_md_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            (config_dir / "SYSTEM.md").write_text(
                "Loaded from file.", encoding="utf-8"
            )
            env = MvgeEnvironment.resolve(
                DEFAULT_AGENT_NAME,
                config_dir=config_dir,
                allow_unknown_agent=True,
            )
            prompt = MvgeEnvironment.render_prompt(
                env.resolved_prompt.text,
                spells=env.spell_names or [],
            )
            assert "Loaded from file." in prompt
            assert "You are Mvge" not in prompt

    def test_append_system_md_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            (config_dir / "SYSTEM.md").write_text(
                "File-based agent.", encoding="utf-8"
            )
            caller_dir = config_dir / "caller"
            caller_sys = caller_dir / "system_prompt"
            caller_sys.mkdir(parents=True)
            (caller_sys / "APPEND_SYSTEM.md").write_text(
                "Additive instructions.", encoding="utf-8"
            )
            env = MvgeEnvironment.resolve(
                DEFAULT_AGENT_NAME,
                config_dir=config_dir,
                caller_dir=caller_dir,
                allow_unknown_agent=True,
            )
            prompt = MvgeEnvironment.render_prompt(
                env.resolved_prompt.text,
                spells=env.spell_names or [],
            )
            assert "File-based agent." in prompt
            assert "Additive instructions." in prompt


class TestMvgeConfig:
    def test_default_name(self) -> None:
        agent = Mvge(api_key="k")
        assert agent._name == "default-mvge"

    def test_custom_name(self) -> None:
        agent = Mvge(api_key="k", name="my-agent")
        assert agent._name == "my-agent"

    def test_config_dir_uses_name(self) -> None:
        agent = Mvge(api_key="k", name="custom-agent")
        assert "custom-agent" in str(agent.config_dir)
