"""Approval Rune entry point.

Fail-closed execution gate for mutating spell casts. The rune owns policy,
session state, and audit; presentation lives in the host (GUI/CLI), reached
through RuneAPI.request_approval. The rune never imports the engine, NiceGUI,
or CLI code, never declares spell_gateway, and never touches the global
spell allowlist.
"""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

from mvgeos_runes_approval_rune.gate import ApprovalGate
from mvgeos_runes_approval_rune.policy import PolicyStore
from mvgeos_runes_approval_rune.views import PermissionsView

_RUNES: weakref.WeakKeyDictionary[Any, ApprovalRune] = (
    weakref.WeakKeyDictionary()
)


class ApprovalRune:
    """The Approval Rune: critical spell gate plus permissions surface."""

    def __init__(self, api: RuneAPI, data_dir: Path | None = None) -> None:
        """Initialize the instance."""
        self._api = api
        self.gate = ApprovalGate(api=api, data_dir=data_dir)
        self.views = PermissionsView(self.gate)

    def get_permissions_view(self) -> dict[str, Any]:
        """Plain-data permissions surface for the GUI settings dialog."""
        return self.views.get_permissions_view()

    def revoke_grant(self, kind: str, grant_id: str) -> bool:
        """One-click revoke; kind in allow_rule, deny_rule, project, session."""
        return self.views.revoke_grant(kind, grant_id)

    def uninstall(self) -> None:
        """Host uninstall flow: delete the policy file only; audit survives."""
        store = PolicyStore(self.gate._store.data_dir, self.gate._install_id)  # noqa: SLF001
        try:
            store.policy_path.unlink()
        except FileNotFoundError:
            pass

    def warnings(self) -> list[str]:
        """Policy warnings that must surface loudly, never silently."""
        return list(self.gate.warnings)


def get_rune(api: RuneAPI) -> ApprovalRune | None:
    """Return the ApprovalRune instance bound to this RuneAPI, if any."""
    return _RUNES.get(api)


def rune_factory(api: RuneAPI, data_dir: Path | None = None) -> ApprovalRune:
    """Create the rune, register the critical gate, and hook outcomes.

    Returns the rune instance so the host can reach it through the
    engine-owned ``RuneRunner.get_rune("approval-rune")`` accessor.
    """
    rune = ApprovalRune(api, data_dir=data_dir)
    _RUNES[api] = rune

    register_gate = getattr(api, "register_spell_gate", None)
    if callable(register_gate):
        # Engine-owned critical gate contract (lands with the engine slice).
        register_gate(rune.gate.handle)
    api.on(SigilHook.AFTER_SPELL_RESULT, rune.gate.on_after_spell_result)

    for warning in rune.warnings():
        # Surface policy problems loudly; never a silent log line.
        api.emit_event("approval_rune_warning", {"warning": warning})
    return rune
