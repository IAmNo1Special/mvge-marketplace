"""Phase 3: every indexed rune carries a commit pin and a tree hash.

The installer checks the pinned commit out (so a moved branch still
yields the pinned tree) and refuses the install when the staged tree
hash disagrees with the index. These tests pin the publisher side of
that contract: the fields exist, are well-formed, and the recorded
tree hash matches the tree in this checkout.

The recorded ``commit`` is deliberately NOT asserted equal to HEAD:
after a merge, HEAD moves past the commit the index was cut from, and
that is the normal state. What must hold is that the pin commit's
trees equal the recorded hashes, which the publisher workflow (rune
changes committed, pins regenerated, index committed on its own)
guarantees, and that the recorded hash equals the current tree, which
this test checks directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from marketplace_index import (
    Artifact,
    artifacts,
    compute_tree_hash,
    load_index,
)

RUNES = [a for a in artifacts() if a.kind == "runes"]
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_TREE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _ids() -> list[str]:
    return [a.name for a in RUNES]


def test_rune_section_is_not_empty() -> None:
    assert RUNES, "index.json declares no runes"


@pytest.mark.parametrize("artifact", RUNES, ids=_ids())
def test_rune_entry_carries_commit_and_tree_hash(artifact: Artifact) -> None:
    index = load_index()
    entry = index["runes"][artifact.name]
    commit = entry.get("commit")
    tree_hash = entry.get("tree_hash")
    assert isinstance(commit, str) and _HEX_SHA_RE.fullmatch(commit), (
        f"{artifact.name}: 'commit' must be a full hex SHA, got {commit!r}"
    )
    assert isinstance(tree_hash, str) and _TREE_HASH_RE.fullmatch(tree_hash), (
        f"{artifact.name}: 'tree_hash' must be 'sha256:<hex>', got {tree_hash!r}"
    )
    if "revoked" in entry:
        assert isinstance(entry["revoked"], bool)


@pytest.mark.parametrize("artifact", RUNES, ids=_ids())
def test_recorded_tree_hash_matches_current_tree(artifact: Artifact) -> None:
    index = load_index()
    entry = index["runes"][artifact.name]
    assert entry["tree_hash"] == compute_tree_hash(artifact.path), (
        f"{artifact.name}: index tree_hash is stale; regenerate with "
        "scripts/update_index_pins.py after committing rune changes, "
        "then commit index.json on its own."
    )


def test_engine_hash_implementation_matches_mirror() -> None:
    """The marketplace mirror must equal the engine's canonical hash.

    Skipped when the installed engine predates pinning (released mvge
    before Phase 3): the mirror exists precisely so the index test does
    not depend on it. The parity is then checked at release time and
    was verified by hand against the Phase 3 worktree during development.
    """
    pinning = pytest.importorskip("mvge.runes.pinning")
    for artifact in RUNES:
        assert pinning.compute_rune_tree_hash(artifact.path) == (
            compute_tree_hash(artifact.path)
        ), f"{artifact.name}: engine/marketplace hash implementations disagree"
