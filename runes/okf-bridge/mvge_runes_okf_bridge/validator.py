"""Deterministic conformance validator for OKF v0.2 (§11)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from mvge_runes_okf_bridge.parser import (
    extract_frontmatter_and_body,
)
from mvge_runes_okf_bridge.types import ValidationReport

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACTOR_NEAR_MISS = re.compile(r"^(?:human|process)", re.IGNORECASE)
_ACTOR_SHAPE = re.compile(r"^(?:[^\s:/]+:\S+|\S+/\S+)$")
_CITATIONS_HEADING = re.compile(r"^#{1,6}[ \t]+Citations[ \t]*$", re.MULTILINE)
_FOOTNOTE_RE = re.compile(r"\[\^([^\]\s]+)\]")
_RECOMMENDED = ("title", "description", "tags")


def validate_okf_bundle(
    bundle_dir: Path,
    strict: bool = False,
    max_warnings: int | None = None,
) -> ValidationReport:
    """Validate an OKF bundle against the v0.2 specification (§11)."""
    report = ValidationReport(valid=True)

    if not bundle_dir.is_dir():
        report.add_error("", f"MISSING: OKF directory '{bundle_dir}' not found.")
        return report

    all_files: list[Path] = []
    for root, _dirs, files in os.walk(bundle_dir):
        for f in files:
            if f.endswith(".md"):
                all_files.append(Path(root) / f)

    if not all_files:
        report.add_warning("", "FOUND: 0 concepts. Bundle is empty.")
        return report

    for path in all_files:
        rel = path.relative_to(bundle_dir).as_posix()

        # Reserved file: index.md (§8)
        if path.name == "index.md":
            report.indexes += 1
            _validate_index_file(path, rel, bundle_dir, report)
            continue

        # Reserved file: log.md (§9)
        if path.name == "log.md":
            report.logs += 1
            _validate_log_file(path, rel, report)
            continue

        # Concept file (§4, §11)
        report.concepts += 1
        _validate_concept_file(path, rel, bundle_dir, report)

    # Apply warning gates
    if (
        strict
        and report.warnings
        or max_warnings is not None
        and len(report.warnings) > max_warnings
    ):
        report.valid = False

    return report


def _validate_index_file(
    path: Path, rel: str, bundle_dir: Path, report: ValidationReport
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.add_error(rel, f"Could not read index as UTF-8: {exc}")
        return

    is_root = path.parent.resolve() == bundle_dir.resolve()
    if text.startswith("---"):
        data, _, err = extract_frontmatter_and_body(text)
        if err or not isinstance(data, dict):
            report.add_warning(rel, "Invalid frontmatter in index.md")
            return

        if not is_root:
            report.add_warning(
                rel, "Only bundle-root index.md may contain frontmatter (§8)."
            )
        else:
            # Check version
            ver = data.get("okf_version")
            if ver is not None and str(ver) != "0.2":
                report.add_warning(
                    rel, f"index.md declares target version '{ver}' (expected '0.2')."
                )


def _validate_log_file(path: Path, rel: str, report: ValidationReport) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.add_error(rel, f"Could not read log as UTF-8: {exc}")
        return

    if text.startswith("---"):
        report.add_warning(rel, "log.md must not contain frontmatter (§9).")

    # Check for ISO dates in headings/bullets
    lines = text.splitlines()
    has_date = False
    for line in lines:
        if any(
            _ISO_DATE.match(word)
            for word in line.replace(":", " ").replace("-", " - ").split()
        ):
            has_date = True
            break
    if lines and not has_date:
        report.add_warning(
            rel, "log.md should contain ISO-8601 dated entries (YYYY-MM-DD) (§9)."
        )


def _validate_concept_file(
    path: Path, rel: str, bundle_dir: Path, report: ValidationReport
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.add_error(rel, f"Could not read concept as UTF-8: {exc}")
        return

    data, body, err = extract_frontmatter_and_body(text)
    if err or data is None:
        report.add_error(rel, err or "Invalid YAML frontmatter")
        return

    # Hard error 2: non-empty type (§11)
    raw_type = data.get("type")
    if not raw_type or not str(raw_type).strip():
        report.add_error(rel, "Missing or empty required field 'type' (§11).")
        return

    concept_type = str(raw_type).strip()

    # Soft guidance warnings
    for rec in _RECOMMENDED:
        if rec not in data or not data[rec]:
            report.add_warning(rel, f"Missing recommended field '{rec}' (§4.1).")

    # Check v0.1 legacy fields (§13.1)
    if "timestamp" in data and "generated" not in data:
        report.add_warning(
            rel,
            "Legacy 'timestamp' field detected; use 'generated: { by, at }' in v0.2 (§13.1).",
        )
    if _CITATIONS_HEADING.search(body) and "sources" not in data:
        report.add_warning(
            rel,
            "Legacy '# Citations' section detected; use 'sources' frontmatter in v0.2 (§13.1).",
        )

    # Check actor shapes and near-misses (§5.2, §7)
    verified = data.get("verified")
    if isinstance(verified, dict):
        verified = [verified]
    if isinstance(verified, list):
        for v in verified:
            if isinstance(v, dict) and "by" in v:
                actor = str(v["by"]).strip()
                if _ACTOR_NEAR_MISS.match(actor) and not actor.startswith(
                    ("human:", "process:")
                ):
                    report.add_warning(
                        rel,
                        f"Actor near-miss '{actor}': prefix must be exact lowercase 'human:' or 'process:' (§7).",
                    )

    # Check sources and footnotes (§5.1)
    source_ids: set[str] = set()
    sources = data.get("sources")
    if isinstance(sources, list):
        for s in sources:
            if isinstance(s, dict):
                sid = s.get("id")
                if sid:
                    source_ids.add(str(sid))
                if (
                    "usage_count" in s
                    and "usage_window" not in data
                    and "usage_window" not in s
                ):
                    report.add_warning(
                        rel,
                        f"Source '{sid}' has usage_count without a framing usage_window (§5.1).",
                    )

    # Footnotes join check
    for fn in _FOOTNOTE_RE.findall(body):
        if source_ids and fn not in source_ids:
            report.add_warning(
                rel, f"Footnote '[^{fn}]' names no matching source in 'sources' (§5.1)."
            )

    # Asset reference checks (§6.3): warn on dangling references.
    # Broken links are tolerated by consumers, so this never errors.
    asset_refs: list[str] = []
    resource = data.get("resource")
    if isinstance(resource, str) and resource.strip():
        asset_refs.append(resource.strip())
    if isinstance(sources, list):
        for s in sources:
            if isinstance(s, dict):
                res = s.get("resource")
                if isinstance(res, str) and res.strip():
                    asset_refs.append(res.strip())
    for ref in asset_refs:
        if ref.startswith(("http://", "https://", "data:")):
            continue
        target = ref.split("#")[0].split("?")[0].lstrip("/")
        if not target:
            continue
        if not (bundle_dir / target).exists():
            report.add_warning(rel, f"Resource '{ref}' not found in bundle (§6.3).")

    # Attested computation check (§10)
    if concept_type == "Attested Computation" and (
        "runtime" not in data or not str(data["runtime"]).strip()
    ):
        report.add_warning(
            rel, "Attested Computation concept missing required 'runtime' (§10.1)."
        )
