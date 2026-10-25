"""The gate's data directory must be the global layer, and only that.

Grants and audit records are user-owned state that must land where the
engine's installer expects them. The uninstall path is what makes this
security-relevant rather than merely untidy: ``rune uninstall approval-rune``
purges ``global_agents_dir()/approval/policy.toml``, so a gate that wrote
anywhere else leaves the grants the uninstall claims to delete in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mvge.core import (
    GLOBAL_DIR_ENV,
    approval_dir,
    global_agents_dir,
)
from mvge.runes.installer import APPROVAL_RUNE_NAME, _approval_dir

from mvgeos_runes_approval_rune.contracts import SpellIdentity
from mvgeos_runes_approval_rune.gate import ApprovalGate, default_data_dir
from mvgeos_runes_approval_rune.policy import DenyRule


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


INSTALL_ID = "install-1"


def test_default_data_dir_follows_the_global_layer(
    relocated_global: Path,
) -> None:
    """A relocated global dir moves the gate's data directory with it."""
    assert default_data_dir() == relocated_global / "approval"
    assert default_data_dir() == approval_dir()


def test_default_data_dir_defaults_to_home_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override the gate agrees with the engine's own resolver."""
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)
    assert default_data_dir() == global_agents_dir() / "approval"
    assert default_data_dir() == Path("~/.agents").expanduser() / "approval"


def test_default_data_dir_is_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read the override per call, not once at import.

    A module constant freezes whatever the environment held at first import,
    which reinstates the original bug for any process that sets the variable
    later.
    """
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)
    assert default_data_dir() == Path("~/.agents").expanduser() / "approval"

    relocated = tmp_path / "relocated"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(relocated))
    assert default_data_dir() == relocated / "approval"


def test_gate_writes_policy_under_the_relocated_global_layer(
    relocated_global: Path,
) -> None:
    """An end-to-end grant lands under the override, not under home."""
    gate = ApprovalGate(data_dir=None, install_id=INSTALL_ID)
    gate.policy.always_deny.append(
        DenyRule(
            spell=SpellIdentity(
                name="bash",
                source_kind="builtin",
                source_id="coding_mvge",
                source_scope="agent",
                code_digest="digest-1",
            ),
            id="deny-0",
        )
    )
    assert gate._save_policy() is True

    policy_file = relocated_global / "approval" / "policy.toml"
    assert policy_file.is_file(), "policy was not written under the override"
    assert "install-1" in policy_file.read_text(encoding="utf-8")


def test_gate_and_uninstaller_agree_on_the_purge_target(
    relocated_global: Path,
) -> None:
    """The acceptance criterion: uninstall purges what the gate writes.

    Asserted against the installer's own resolver rather than a restatement
    of it, so the two cannot drift apart silently again.
    """
    gate = ApprovalGate(data_dir=None, install_id=INSTALL_ID)

    write_target = gate._store.policy_path
    purge_target = _approval_dir() / "policy.toml"

    assert write_target == purge_target
    assert write_target == relocated_global / "approval" / "policy.toml"
    assert write_target.parent == _approval_dir() == approval_dir()
    assert APPROVAL_RUNE_NAME == "approval-rune"


def test_audit_log_also_follows_the_relocated_global_layer(
    relocated_global: Path,
) -> None:
    """Audit records sit beside the policy, under the same root."""
    gate = ApprovalGate(data_dir=None, install_id=INSTALL_ID)
    gate.audit.append_decision({"decision": "deny", "spell": {"name": "bash"}})

    audit_file = relocated_global / "approval" / "audit.jsonl"
    assert audit_file.is_file()
    assert gate.audit.audit_path == audit_file


def test_explicit_data_dir_still_wins(tmp_path: Path) -> None:
    """An explicit directory keeps priority over the resolved default.

    The fix changes the fallback, not the override: callers that already pass
    a data directory must be unaffected.
    """
    explicit = tmp_path / "elsewhere"
    gate = ApprovalGate(data_dir=explicit, install_id=INSTALL_ID)
    assert gate._store.policy_path == explicit / "policy.toml"
