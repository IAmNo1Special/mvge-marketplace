"""Session-title state machine, driven through its hooks and commands.

The fake runner records every name it is given, so each test asserts on
the name the session actually ends up with, not on internal bookkeeping.
The LLM test uses a real RuneRunner (the path the rune's own
``isinstance`` check gates on) with the realm registry patched out, so no
test ever calls a real provider.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneContext, SigilHook

_RUNE_PATH = Path(__file__).resolve().parent.parent / "rune.py"
_spec = importlib.util.spec_from_file_location("session_title_rune_entry", _RUNE_PATH)
assert _spec is not None and _spec.loader is not None
rune_module = importlib.util.module_from_spec(_spec)
# Dataclasses resolve their module from sys.modules at class creation,
# so the module must be registered before it is executed.
sys.modules["session_title_rune_entry"] = rune_module
_spec.loader.exec_module(rune_module)


class FakeRunner:
    """A runner that records session names instead of applying them."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.context = type("Context", (), {"model_id": "", "api_key": ""})()

    def set_session_name(self, name: str) -> None:
        self.names.append(name)


class FakeAPI:
    """The RuneAPI surface SessionTitleRune actually touches."""

    def __init__(self) -> None:
        self._runner = FakeRunner()
        self.hooks: dict[Any, Any] = {}
        self.commands: dict[str, Any] = {}

    def on(self, hook: Any, handler: Any) -> None:
        self.hooks[hook] = handler

    def register_command(
        self, name: str, description: str = "", handler: Any = None
    ) -> None:
        self.commands[name] = handler


def _make_rune() -> tuple[FakeAPI, Any]:
    api = FakeAPI()
    rune = rune_module.SessionTitleRune(api)
    return api, rune


async def _drain_llm(rune: Any) -> None:
    """Wait out the background title task, if one was scheduled."""
    for _ in range(100):
        if not rune.state.llm_pending:
            return
        await asyncio.sleep(0)
    raise AssertionError("LLM title task never finished")


def test_factory_registers_all_hooks_and_both_commands() -> None:
    """The rune must be reachable on every lifecycle hook it declares."""
    api = FakeAPI()
    rune_module.rune_factory(api)
    assert set(api.hooks) == {
        SigilHook.SESSION_START,
        SigilHook.INPUT,
        SigilHook.TURN_END,
        SigilHook.AGENT_END,
        SigilHook.SESSION_SHUTDOWN,
    }
    assert set(api.commands) == {"session-name", "session-rename"}


@pytest.mark.asyncio
async def test_first_input_sets_the_fallback_title() -> None:
    """The first user prompt names the session immediately, for free."""
    api, rune = _make_rune()
    await rune.on_input({"content": "fix the login bug that locks users out"})
    assert rune.state.name == "fix the login bug that locks users out"
    assert rune.state.source == "fallback"
    assert rune.state.revision == 1
    assert api._runner.names == ["fix the login bug that locks users out"]
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_second_input_does_not_overwrite_the_fallback() -> None:
    """Later prompts count toward LLM eligibility; they never rename."""
    api, rune = _make_rune()
    await rune.on_input({"content": "fix the login bug"})
    await rune.on_input({"content": "also check the signup page"})
    assert rune.state.name == "fix the login bug"
    assert rune.state.eligible_count == 2
    assert api._runner.names == ["fix the login bug"]
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_input_with_no_content_does_nothing() -> None:
    """An input with no text is not a prompt and must not name anything."""
    api, rune = _make_rune()
    await rune.on_input({})
    await rune.on_input({"content": ""})
    assert rune.state.name is None
    assert rune.state.first_user_text is None
    assert api._runner.names == []


@pytest.mark.asyncio
async def test_text_blocks_join_into_the_fallback() -> None:
    """A multi-block first message still yields one title from its text."""
    _, rune = _make_rune()
    await rune.on_input(
        {
            "content": [
                {"type": "text", "text": "refactor the"},
                {"type": "image", "text": "ignored"},
                {"type": "text", "text": "auth module"},
            ]
        }
    )
    assert rune.state.name == "refactor the auth module"
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_user_pin_wins_and_stops_later_renames() -> None:
    """A name the user sets is pinned: later prompts never override it."""
    api, rune = _make_rune()
    await rune.on_input({"content": "fix the login bug"})
    rune.set_session_name("My Project")
    assert rune.state.name == "My Project"
    assert rune.state.source == "user"
    assert rune.state.user_pinned is True
    assert rune.state.llm_pending is False
    await rune.on_input({"content": "something completely different"})
    assert rune.state.name == "My Project"
    assert api._runner.names[-1] == "My Project"
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_session_start_resets_the_state() -> None:
    """A new session starts unnamed, not carrying the last session's title."""
    _, rune = _make_rune()
    await rune.on_input({"content": "fix the login bug"})
    await rune.on_session_start(None)
    assert rune.state.name is None
    assert rune.state.source is None
    assert rune.state.revision == 0
    assert rune.state.fallback_done is False
    assert rune.state.first_user_text is None
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_commands_get_and_rename_the_session() -> None:
    """The session-name / session-rename commands round-trip through the factory."""
    api = FakeAPI()
    rune_module.rune_factory(api)
    assert api.commands["session-name"]() is None
    api.commands["session-rename"]({"title": "Renamed Session"})
    assert api.commands["session-name"]() == "Renamed Session"
    assert api._runner.names == ["Renamed Session"]


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pending_llm_task() -> None:
    """Shutdown must end a stuck background title task, not leak it."""
    _, rune = _make_rune()

    async def _never_returns() -> None:
        await asyncio.sleep(60)

    rune._call_llm_for_title = _never_returns
    await rune.on_input({"content": "fix the login bug"})
    assert rune.state.llm_pending is True
    await rune.on_session_shutdown(None)
    assert rune.state.llm_pending is False
    assert rune._task is None or rune._task.done()


@pytest.mark.asyncio
async def test_llm_title_upgrades_the_fallback() -> None:
    """With a real runner and a patched registry, the LLM title replaces
    the fallback and is the name the session ends up with."""
    context = RuneContext(
        cwd="/fake/cwd", model_id="openrouter/test-model", api_key="test-key"
    )
    runner = RuneRunner()
    runner.bind_context(context)
    api = RuneAPI(runner, rune_name="session-title")
    rune = rune_module.SessionTitleRune(api)

    fake_realm = type(
        "FakeRealm",
        (),
        {"call": AsyncMock(return_value="  Refined Session Title  ")},
    )()
    fake_registry = type(
        "FakeRegistry", (), {"create_realm": lambda self, *a, **k: fake_realm}
    )()

    with patch("mvge.provider.registry.RealmRegistry", return_value=fake_registry):
        await rune.on_input({"content": "fix the login bug"})
        await _drain_llm(rune)

    assert rune.state.name == "Refined Session Title"
    assert rune.state.source == "provider"
    assert rune.state.revision == 2
    fake_realm.call.assert_awaited_once()
    assert fake_realm.call.await_args.kwargs["max_tokens"] == 50
    await rune.on_session_shutdown(None)


class _TypedSessionStart:
    """A typed SESSION_START payload, as the engine's dataclass will carry it."""

    def __init__(self, existing_session_name: Any) -> None:
        self.session_name = "tome-id"
        self.existing_session_name = existing_session_name


@pytest.mark.asyncio
async def test_session_start_with_existing_title_adopts_it_from_dict() -> None:
    """A resumed session adopts the persisted title instead of starting unnamed."""
    api, rune = _make_rune()
    await rune.on_input({"content": "fix the login bug"})
    await rune.on_session_start({"existing_session_name": "  Old Persisted Title  "})
    assert rune.state.name == "Old Persisted Title"
    assert rune.state.source == "restored"
    assert rune.state.user_pinned is False
    assert rune.state.revision == 0
    # Adoption is in-memory only: the engine already owns the persisted
    # name, so the rune must not re-persist or overwrite it.
    assert api._runner.names == ["fix the login bug"]
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_session_start_with_existing_title_adopts_it_from_typed_data() -> None:
    """The restore reads typed sigil data, not just raw dicts."""
    api, rune = _make_rune()
    await rune.on_session_start(_TypedSessionStart("Typed Persisted Title"))
    assert rune.state.name == "Typed Persisted Title"
    assert rune.state.source == "restored"
    assert rune.state.user_pinned is False
    assert api._runner.names == []
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_no_fallback_after_restore() -> None:
    """Resumed prompts never replace the restored title with a fallback."""
    api, rune = _make_rune()
    await rune.on_session_start({"existing_session_name": "Original Title"})
    await rune.on_input({"content": "do something completely different now"})
    await rune.on_input({"content": "and then another unrelated thing"})
    assert rune.state.name == "Original Title"
    assert rune.state.source == "restored"
    assert api._runner.names == []
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_no_llm_title_after_restore_on_later_turns() -> None:
    """The LLM path must not re-break a resumed session on a later turn.

    The second resumed prompt is the dangerous one: without a restore
    guard it reaches the LLM scheduler even when the fallback is blocked.
    """
    _, rune = _make_rune()
    await rune.on_session_start({"existing_session_name": "Original Title"})
    rune._call_llm_for_title = AsyncMock()
    await rune.on_input({"content": "first resumed prompt"})
    await rune.on_input({"content": "second resumed prompt"})
    await rune.on_input({"content": "third resumed prompt"})
    await rune.on_turn_end({})
    await _drain_llm(rune)
    assert rune.state.name == "Original Title"
    assert rune.state.source == "restored"
    assert rune.state.llm_pending is False
    assert rune._task is None
    rune._call_llm_for_title.assert_not_awaited()
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_rename_after_restore_still_overwrites() -> None:
    """Restore is not a pin: a deliberate rename after restore wins."""
    api, rune = _make_rune()
    await rune.on_session_start({"existing_session_name": "Original Title"})
    rune.set_session_name("Deliberate Rename")
    assert rune.state.name == "Deliberate Rename"
    assert rune.state.source == "user"
    assert rune.state.user_pinned is True
    assert api._runner.names == ["Deliberate Rename"]
    await rune.on_input({"content": "something completely different"})
    assert rune.state.name == "Deliberate Rename"
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_session_start_without_existing_title_behaves_as_today() -> None:
    """Absent or None existing title leaves fresh-session behavior unchanged."""
    for payload in (None, {}, {"existing_session_name": None}):
        api, rune = _make_rune()
        await rune.on_session_start(payload)
        assert rune.state.name is None
        await rune.on_input({"content": "fix the login bug"})
        assert rune.state.name == "fix the login bug"
        assert rune.state.source == "fallback"
        assert api._runner.names == ["fix the login bug"]
        await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_session_start_with_blank_existing_title_behaves_as_today() -> None:
    """A blank existing title is no title: fresh generation proceeds."""
    _, rune = _make_rune()
    await rune.on_session_start({"existing_session_name": "   "})
    assert rune.state.name is None
    await rune.on_input({"content": "fix the login bug"})
    assert rune.state.name == "fix the login bug"
    await rune.on_session_shutdown(None)


@pytest.mark.asyncio
async def test_llm_failure_leaves_the_fallback_in_place() -> None:
    """A provider failure must never crash the session or clear its title."""
    context = RuneContext(
        cwd="/fake/cwd", model_id="openrouter/test-model", api_key="test-key"
    )
    runner = RuneRunner()
    runner.bind_context(context)
    api = RuneAPI(runner, rune_name="session-title")
    rune = rune_module.SessionTitleRune(api)

    failing_realm = type(
        "FailingRealm",
        (),
        {"call": AsyncMock(side_effect=RuntimeError("provider down"))},
    )()
    failing_registry = type(
        "FailingRegistry",
        (),
        {"create_realm": lambda self, *a, **k: failing_realm},
    )()

    with patch("mvge.provider.registry.RealmRegistry", return_value=failing_registry):
        await rune.on_input({"content": "fix the login bug"})
        await _drain_llm(rune)

    assert rune.state.name == "fix the login bug"
    assert rune.state.source == "fallback"
    assert rune.state.llm_pending is False
    await rune.on_session_shutdown(None)
