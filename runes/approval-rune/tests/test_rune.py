"""Tests for the rune factory wiring."""

import importlib.util
from pathlib import Path
from typing import Any

from mvge.runes.types import SigilHook

_RUNE_PATH = Path(__file__).resolve().parent.parent / "rune.py"
_spec = importlib.util.spec_from_file_location(
    "approval_rune_entry", _RUNE_PATH
)
assert _spec is not None and _spec.loader is not None
rune_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rune_module)


class FakeContext:
    """Fake rune context carrying a session id."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.session_id = "sess-1"


class FakeAPI:
    """Fake RuneAPI surface for factory tests."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.context = FakeContext()
        self.install_id = "install-1"
        self.hooks: dict[str, Any] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.gate_handler: Any = None

    def on(self, hook: Any, handler: Any) -> None:
        """On."""
        self.hooks[hook] = handler

    def emit_event(self, name: str, payload: dict[str, Any]) -> None:
        """Emit event."""
        self.events.append((name, payload))

    def register_spell_gate(self, handler: Any) -> None:
        """Register spell gate."""
        self.gate_handler = handler


def test_factory_registers_gate_and_hook(tmp_path: Path) -> None:
    """Test factory registers gate and hook."""
    api = FakeAPI()
    rune_module.rune_factory(api, data_dir=tmp_path)
    rune = rune_module.get_rune(api)
    assert rune is not None
    assert api.gate_handler == rune.gate.handle
    assert SigilHook.AFTER_SPELL_RESULT in api.hooks
    view = rune.get_permissions_view()
    assert view["session"]["approve_all_active"] is False
    assert rune.revoke_grant("session", "session") is False


def test_factory_without_engine_gate_api_still_hooks(tmp_path: Path) -> None:
    """Test factory without engine gate api still hooks."""

    class NoGateAPI(FakeAPI):
        """FakeAPI without register_spell_gate (pre-engine)."""

        # No register_spell_gate: the engine slice has not landed yet.
        register_spell_gate = None  # type: ignore[assignment]

    api = NoGateAPI()
    rune_module.rune_factory(api, data_dir=tmp_path)
    assert SigilHook.AFTER_SPELL_RESULT in api.hooks


def test_factory_surfaces_policy_warnings(tmp_path: Path) -> None:
    """Test factory surfaces policy warnings."""
    (tmp_path / "policy.toml").write_text("schema_version = [[[\n")
    api = FakeAPI()
    rune_module.rune_factory(api, data_dir=tmp_path)
    assert any(name == "approval_rune_warning" for name, _ in api.events), (
        "malformed policy must surface a loud warning, never a silent log"
    )


def test_uninstall_deletes_policy_but_keeps_audit(tmp_path: Path) -> None:
    """Test uninstall deletes policy but keeps audit."""
    api = FakeAPI()
    rune_module.rune_factory(api, data_dir=tmp_path)
    rune = rune_module.get_rune(api)
    assert rune is not None
    rune.gate._store.save(rune.gate.policy)  # noqa: SLF001
    rune.gate.audit.append_decision({"cast_id": "call_1", "decision": "deny"})
    rune.uninstall()
    assert not (tmp_path / "policy.toml").exists()
    assert (tmp_path / "audit.jsonl").exists()


def test_factory_returns_rune_instance(tmp_path: Path) -> None:
    """The factory returns its instance for the host get_rune accessor."""
    api = FakeAPI()
    rune = rune_module.rune_factory(api, data_dir=tmp_path)
    assert rune is rune_module.get_rune(api)
    assert rune.get_permissions_view()["session"]["approve_all_active"] is False
