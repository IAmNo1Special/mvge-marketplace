"""Regenerate the Phase 3 pin fields of ``index.json`` (runes only).

For every rune entry, records:

``commit``
    The marketplace HEAD SHA at generation time: the commit the index
    was cut from. The installer checks this commit out, so a branch that
    moved since the index was cut still yields the pinned tree, never
    the new HEAD.

``tree_hash``
    The Phase 3 tree hash of the rune's directory, computed with
    ``marketplace_index.compute_tree_hash`` (the engine's algorithm).

Workflow, and why the order matters: commit the rune changes first,
then run this script, then commit ``index.json`` on its own. The
recorded commit must contain the hashed trees, and the pin commit
itself must touch only ``index.json`` -- otherwise the commit the
installer checks out would hold different trees than the recorded
hashes, and installs would (correctly, fail-closed) refuse.

The script refuses to run while any ``runes/`` path has uncommitted
changes, because then the recorded HEAD would not contain the tree
being hashed. ``index.json`` itself may be dirty; updating it is the
point.

Index integrity limit: these fields are only as trustworthy as the
index fetch (HTTPS plus repo access control). This is transport trust,
not publisher signing, and a pinned rune is unmodified, not benign.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from marketplace_index import (  # noqa: E402
    INDEX_PATH,
    artifacts,
    compute_tree_hash,
)


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    dirty = _git(["status", "--porcelain", "--", "runes"])
    if dirty:
        print(
            "Refusing: uncommitted changes under runes/ would make the "
            "recorded commit disagree with the hashed trees:\n" + dirty,
            file=sys.stderr,
        )
        return 1
    head = _git(["rev-parse", "HEAD"])
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    runes = index.get("runes")
    if not isinstance(runes, dict):
        print("index.json has no 'runes' section.", file=sys.stderr)
        return 1
    for artifact in artifacts():
        if artifact.kind != "runes":
            continue
        entry = runes.get(artifact.name)
        if not isinstance(entry, dict):
            print(f"index.json has no entry for {artifact.name}.", file=sys.stderr)
            return 1
        entry["commit"] = head
        entry["tree_hash"] = compute_tree_hash(artifact.path)
        print(f"{artifact.name}: commit {head[:12]} tree {entry['tree_hash']}")
    INDEX_PATH.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
