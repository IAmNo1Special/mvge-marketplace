"""Phase 5b whole-process wrapping, marketplace side of the contract.

Wrapping is a deployment posture, not a manifest field and not
a reclassification: no manifest declares it, the Phase 4
hosting table is unchanged by it, and the in-process-only tier
cannot be wrapped into a confinement claim. The engine owns
the wrapping implementation; this test pins the marketplace
side so a manifest edit or a docs drift forces a deliberate
update instead of silently changing the claim.

Skipped when the installed engine predates Phase 5b (released
mvge before wrapping landed): the gate then runs against the
engine that carries it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _manifests() -> dict[str, dict]:
    result = {}
    for manifest_path in sorted((REPO_ROOT / "runes").glob("*/manifest.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        result[data["name"]] = data
    return result


def test_no_manifest_declares_wrapping() -> None:
    for name, data in _manifests().items():
        assert "wrapping" not in data, (
            f"{name}: wrapping is a deployment posture, not a manifest "
            "field; an operator enables it per deployment, a rune "
            "cannot opt itself in or out"
        )


def test_wrapping_is_opt_in_by_default() -> None:
    wrapping = pytest.importorskip("mvge.runes.wrapping")
    policy = wrapping.WrappingPolicy(workspace_root=Path("/tmp"))
    assert policy.enabled is False
    assert wrapping.wrap_host_command(["python", "-m", "x"], policy) == [
        "python",
        "-m",
        "x",
    ]
    assert wrapping.WRAPPING_UNAVAILABLE == "WRAPPING_UNAVAILABLE"


def test_wrapping_refuses_the_in_process_tier(tmp_path: Path) -> None:
    wrapping = pytest.importorskip("mvge.runes.wrapping")
    from mvge.runes.manifest import load_manifest

    manifest = load_manifest(REPO_ROOT / "runes" / "approval-rune")
    assert manifest is not None
    policy = wrapping.WrappingPolicy(
        enabled=True,
        backend="bwrap",
        workspace_root=tmp_path,
    )
    with pytest.raises(wrapping.WrappingUnavailableError):
        wrapping.validate_wrapping_policy(policy, manifest=manifest)


def test_wrapping_accepts_a_hostable_rune_policy(tmp_path: Path) -> None:
    pytest.importorskip("mvge.runes.wrapping")
    from mvge.runes.manifest import load_manifest

    manifest = load_manifest(REPO_ROOT / "runes" / "adr-bridge")
    assert manifest is not None
    # Validation of the manifest half only: backend availability
    # is platform-dependent, so a missing bwrap must not fail
    # this marketplace gate; the engine suite covers backends.
    import shutil

    if shutil.which("bwrap") is None:
        pytest.skip("bwrap not installed here")
    from mvge.runes.wrapping import WrappingPolicy, validate_wrapping_policy

    policy = WrappingPolicy(
        enabled=True,
        backend="bwrap",
        workspace_root=tmp_path,
    )
    assert validate_wrapping_policy(policy, manifest=manifest) is policy


def test_wrapping_docs_state_the_matrix_and_windows_gap() -> None:
    doc = REPO_ROOT / "docs" / "runes" / "whole-process-wrapping-5b.md"
    assert doc.is_file(), "Phase 5b marketplace doc is missing"
    text = doc.read_text(encoding="utf-8")
    for required in ("bwrap", "docker", "Windows", "write-confined"):
        assert required in text, f"wrapping doc must name {required!r}"
    assert "all-or-none" in text, "wrapping doc must state the network gap"
    assert "verify_wrapped_deployment" in text
