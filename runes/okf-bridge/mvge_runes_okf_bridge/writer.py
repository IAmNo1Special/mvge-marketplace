"""Atomic concept lifecycle writes for OKF bundles.

Every mutation is atomic (temp file + os.replace), keeps a timestamped
backup under <config>/.backups/knowledge/<concept-id>/ (newest 10 kept),
and appends an entry to the bundle log.md.

Update semantics: write_concept replaces the descriptive fields with the
given values and re-stamps `generated`; `verified` and `status` are always
preserved from the existing file (trust is never silently dropped), and
`context` is preserved unless explicitly passed.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from mvge_runes_okf_bridge.graph import RESERVED_FILENAMES
from mvge_runes_okf_bridge.parser import (
    extract_frontmatter_and_body,
    parse_concept_file,
)
from mvge_runes_okf_bridge.types import Concept

CONTEXT_VALUES = ("auto", "search-only")
MAX_BACKUPS_PER_CONCEPT = 10
DEFAULT_GENERATED_BY = "mvge/okf-bridge"


def ensure_bundle(root: Path) -> Path:
    """Create the bundle dir with root index.md / log.md if missing."""
    root.mkdir(parents=True, exist_ok=True)
    index = root / "index.md"
    if not index.exists():
        index.write_text(
            '---\nokf_version: "0.2"\n---\n\n# Knowledge Bundle\n',
            encoding="utf-8",
        )
    log = root / "log.md"
    if not log.exists():
        log.write_text("# Directory Update Log\n", encoding="utf-8")
    return root


def _resolve_target(root: Path, concept_id: str) -> Path:
    """Validate a concept id and resolve it to a file inside the bundle."""
    cid = concept_id.strip().removesuffix(".md")
    if not cid:
        raise ValueError("concept_id must not be empty")
    posix = PurePosixPath(cid)
    if posix.is_absolute():
        raise ValueError(f"Absolute concept_id is not allowed: {concept_id!r}")
    if ".." in posix.parts:
        raise ValueError(f"concept_id must not escape the bundle: {concept_id!r}")
    target = (root / posix).with_suffix(".md")
    # Belt and braces: the resolved path must stay inside the bundle root.
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"concept_id must not escape the bundle: {concept_id!r}")
    return target


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _backup_previous(root: Path, concept_id: str, target: Path) -> None:
    """Copy the previous version into the rotating backup area."""
    if not target.is_file():
        return
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_dir = root.parent / ".backups" / "knowledge" / concept_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(backup_dir / f"{stamp}.md", target.read_text(encoding="utf-8"))
    existing = sorted(backup_dir.glob("*.md"))
    for stale in existing[:-MAX_BACKUPS_PER_CONCEPT]:
        stale.unlink()


def _append_log(root: Path, kind: str, concept_id: str, detail: str) -> None:
    log = root / "log.md"
    text = (
        log.read_text(encoding="utf-8") if log.is_file() else "# Directory Update Log\n"
    )
    today = datetime.now(UTC).date().isoformat()
    if f"## {today}" not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n## {today}\n"
    if not text.endswith("\n"):
        text += "\n"
    text += f"* **{kind}**: concept `{concept_id}` {detail}.\n"
    _atomic_write_text(log, text)


def _refresh_index(root: Path) -> None:
    """Rebuild the ``## Concepts`` listing in the bundle-root index.md.

    Per OKF v0.2 §3.1 the bundle-root index.md is the directory listing;
    it is regenerated (not patched) after every write/deprecate so it can
    never drift from the files on disk. Unparseable files are skipped.
    """
    index = root / "index.md"
    text = index.read_text(encoding="utf-8") if index.is_file() else ""

    # Strip any existing Concepts section (up to the next ## heading).
    kept: list[str] = []
    skipping = False
    for line in text.split("\n"):
        if line.startswith("## Concepts"):
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)

    entries: list[str] = []
    for md in sorted(root.rglob("*.md")):
        if md.name in RESERVED_FILENAMES:
            continue
        concept, _err = parse_concept_file(md, root)
        if concept is None:
            continue
        rel = md.relative_to(root).as_posix()
        label = concept.title or concept.id
        marker = " (deprecated)" if concept.status == "deprecated" else ""
        entries.append(f"- [{label}]({rel}){marker}")

    body = "\n".join(kept).rstrip("\n")
    section = "## Concepts\n\n" + "\n".join(entries) + "\n" if entries else ""
    if body and section:
        body += "\n\n"
    _atomic_write_text(index, body + section)


def _coerce_sources(
    sources: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]] | None:
    """Validate source dicts against the OKF v0.2 §5.1 shape."""
    if sources is None:
        return None
    coerced: list[dict[str, Any]] = []
    for s in sources:
        if not isinstance(s, dict) or "id" not in s or "resource" not in s:
            raise ValueError(
                "each source must be a dict with 'id' and 'resource' (OKF v0.2 §5.1)"
            )
        coerced.append(dict(s))
    return coerced


def _dump_concept_file(data: dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    text = f"---\n{frontmatter}---\n{body}"
    if not text.endswith("\n"):
        text += "\n"
    return text


def _read_existing(root: Path, target: Path) -> tuple[dict[str, Any], str]:
    if not target.is_file():
        raise FileNotFoundError(f"Concept file does not exist: {target}")
    text = target.read_text(encoding="utf-8")
    data, body, err = extract_frontmatter_and_body(text)
    if err or data is None:
        raise ValueError(f"Existing concept is not parseable: {err}")
    return data, body


def _finish_write(
    root: Path, concept_id: str, target: Path, data: dict[str, Any], body: str
) -> Concept:
    _backup_previous(root, concept_id, target)
    _atomic_write_text(target, _dump_concept_file(data, body))
    concept, err = parse_concept_file(target, root)
    if concept is None:
        raise ValueError(f"Wrote an unparseable concept file: {err}")
    return concept


def write_concept(
    root: Path,
    concept_id: str,
    *,
    type: str,
    title: str = "",
    description: str = "",
    body: str = "",
    tags: list[str] | tuple[str, ...] = (),
    context: str | None = None,
    stale_after: str | None = None,
    resource: str | None = None,
    sources: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    generated_by: str = DEFAULT_GENERATED_BY,
) -> Concept:
    """Create or update a concept, stamping generated with the writing actor.

    New concepts default to `context: search-only`; updates preserve the
    existing context unless explicitly passed. `verified`, `status`,
    `resource`, and `sources` are always preserved from the existing file
    unless explicitly passed. `resource`/`sources` follow OKF v0.2 §6.3:
    bundle-relative asset paths (each source needs `id` and `resource`).
    """
    if not type or not str(type).strip():
        raise ValueError("type is required and must be non-empty (OKF v0.2 §11)")
    if context is not None and context not in CONTEXT_VALUES:
        raise ValueError(f"context must be one of {CONTEXT_VALUES}")

    ensure_bundle(root)
    target = _resolve_target(root, concept_id)
    cid = target.relative_to(root).as_posix().removesuffix(".md")

    data: dict[str, Any] = {
        "type": str(type).strip(),
        "title": title,
        "description": description,
        "tags": list(tags),
        "context": context if context is not None else "search-only",
        "status": "stable",
    }
    if stale_after:
        data["stale_after"] = stale_after
    coerced_sources = _coerce_sources(sources)
    if resource is not None:
        data["resource"] = resource
    if coerced_sources is not None:
        data["sources"] = coerced_sources
    data["generated"] = {"by": generated_by, "at": _utc_now()}

    if target.is_file():
        old_data, _old_body = _read_existing(root, target)
        # Trust, lifecycle, and asset references survive a rewrite; they
        # change only via verify_concept / deprecate_concept or explicit args.
        if "verified" in old_data:
            data["verified"] = old_data["verified"]
        if "status" in old_data:
            data["status"] = old_data["status"]
        if context is None and "context" in old_data:
            data["context"] = old_data["context"]
        if resource is None and "resource" in old_data:
            data["resource"] = old_data["resource"]
        if coerced_sources is None and "sources" in old_data:
            data["sources"] = old_data["sources"]

    concept = _finish_write(root, cid, target, data, body)
    _append_log(root, "Update", cid, f"written by {generated_by}")
    _refresh_index(root)
    return concept


def verify_concept(root: Path, concept_id: str, *, by: str) -> Concept:
    """Append a verification entry, moving the concept up the trust ladder."""
    if not by or not by.strip():
        raise ValueError("by (the verifying actor) is required")
    target = _resolve_target(root, concept_id)
    cid = target.relative_to(root).as_posix().removesuffix(".md")
    data, body = _read_existing(root, target)

    verified = data.get("verified")
    entries: list[dict[str, str]] = []
    if isinstance(verified, dict):
        entries.append({str(k): str(v) for k, v in verified.items()})
    elif isinstance(verified, list):
        for item in verified:
            if isinstance(item, dict):
                entries.append({str(k): str(v) for k, v in item.items()})
    entries.append({"by": by.strip(), "at": _utc_now()})
    data["verified"] = entries

    concept = _finish_write(root, cid, target, data, body)
    _append_log(root, "Verification", cid, f"verified by {by.strip()}")
    return concept


def deprecate_concept(root: Path, concept_id: str) -> Concept:
    """Mark a concept deprecated. The file is preserved for links/history."""
    target = _resolve_target(root, concept_id)
    cid = target.relative_to(root).as_posix().removesuffix(".md")
    data, body = _read_existing(root, target)
    data["status"] = "deprecated"
    concept = _finish_write(root, cid, target, data, body)
    _append_log(root, "Deprecation", cid, "marked deprecated")
    _refresh_index(root)
    return concept


def set_concept_context(root: Path, concept_id: str, context: str) -> Concept:
    """Flip a concept between auto-injected and search-only."""
    if context not in CONTEXT_VALUES:
        raise ValueError(f"context must be one of {CONTEXT_VALUES}")
    target = _resolve_target(root, concept_id)
    cid = target.relative_to(root).as_posix().removesuffix(".md")
    data, body = _read_existing(root, target)
    data["context"] = context
    concept = _finish_write(root, cid, target, data, body)
    _append_log(root, "Update", cid, f"context set to {context}")
    return concept
