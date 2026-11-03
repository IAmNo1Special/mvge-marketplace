"""Phase 4 v1 hosting gate for first-party runes.

Classification is engine-side; this test pins the marketplace
side of the contract so a manifest edit (or a new rune) forces
a deliberate update to docs/runes/hosting-v1.md instead of
silently changing which runes the engine hosts.

Skipped when the installed engine predates Phase 4 (released
mvge before the host landed): the gate then runs against the
engine that carries it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

IN_PROCESS_ONLY = {
    "approval-rune",
    "opencode-realm",
    "openrouter-realm",
    "pollinations-realm",
    "seeker",
    "session-title",
    "pi-codec",
    "codeact-rune",
    "submvge-bridge",
    "mcp-bridge",
}

ELIGIBLE = {
    "adr-bridge",
    "checkpoint-bridge",
    "eval-bridge",
    "lsp-bridge",
    "okf-bridge",
    "opentelemetry-bridge",
    "selfmod-bridge",
    "session-search",
    "skill-evolution",
    "skills-bridge",
    "steering-bridge",
}


def _manifests() -> dict[str, dict]:
    result = {}
    for manifest_path in sorted((REPO_ROOT / "runes").glob("*/manifest.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        result[data["name"]] = data
    return result


def test_every_rune_is_classified() -> None:
    manifests = _manifests()
    assert IN_PROCESS_ONLY | ELIGIBLE == set(manifests), (
        "Unclassified runes (update this test and "
        "docs/runes/hosting-v1.md deliberately): "
        f"{sorted(set(manifests) - IN_PROCESS_ONLY - ELIGIBLE)}; "
        f"stale entries: {sorted((IN_PROCESS_ONLY | ELIGIBLE) - set(manifests))}"
    )


def test_hosting_field_values_are_valid() -> None:
    for name, data in _manifests().items():
        hosting = data.get("hosting")
        assert hosting in (None, "hosted", "in-process"), (
            f"{name}: hosting must be absent, 'hosted', or "
            f"'in-process', got {hosting!r}"
        )


def test_engine_classification_matches_the_table() -> None:
    classify = pytest.importorskip("mvge.runes.host_classification")
    from mvge.runes.manifest import load_manifest

    for name in sorted(IN_PROCESS_ONLY):
        manifest = load_manifest(REPO_ROOT / "runes" / name)
        assert manifest is not None, f"{name}: manifest did not parse"
        verdict = classify.classify_manifest(manifest)
        assert not verdict.hostable, f"{name}: expected in-process-only"
        assert verdict.tier == "in-process-only"
    for name in sorted(ELIGIBLE):
        manifest = load_manifest(REPO_ROOT / "runes" / name)
        assert manifest is not None, f"{name}: manifest did not parse"
        verdict = classify.classify_manifest(manifest)
        assert verdict.hostable, f"{name}: expected hostable, got {verdict.reason}"
        assert verdict.tier == "hosted"


def test_no_tier_rune_declares_hosted() -> None:
    for name in sorted(IN_PROCESS_ONLY):
        hosting = _manifests()[name].get("hosting")
        assert hosting != "hosted", (
            f"{name} is in the in-process-only tier and must not "
            "declare hosting: hosted"
        )
