"""In-place migration of OKF v0.1 bundles to v0.2 (§13.1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_TIMESTAMP_RE = re.compile(r"^timestamp:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_CITATIONS_SECTION_RE = re.compile(
    r"^#{1,6}[ \t]+Citations[ \t]*\r?\n(.*?)(?=^#{1,6}|\Z)", re.MULTILINE | re.DOTALL
)
_MD_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)


def migrate_bundle_in_place(bundle_dir: Path) -> list[str]:
    """Migrate all v0.1 files in an OKF bundle to v0.2.

    Returns list of modified relative file paths.
    """
    if not bundle_dir.is_dir():
        return []

    modified: list[str] = []

    for root, _dirs, files in os.walk(bundle_dir):
        for f in files:
            if not f.endswith(".md"):
                continue

            path = Path(root) / f
            rel = path.relative_to(bundle_dir).as_posix()

            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Check index.md
            if f == "index.md" and path.parent.resolve() == bundle_dir.resolve():
                new_content = _migrate_root_index(content)
                if new_content != content:
                    path.write_text(new_content, encoding="utf-8")
                    modified.append(rel)
                continue

            if f in ("index.md", "log.md"):
                continue

            new_content = _migrate_concept(content)
            if new_content != content:
                path.write_text(new_content, encoding="utf-8")
                modified.append(rel)

    return modified


def _migrate_root_index(text: str) -> str:
    if text.startswith("---"):
        if (
            'okf_version: "0.1"' in text
            or "okf_version: '0.1'" in text
            or "okf_version: 0.1" in text
        ):
            return re.sub(
                r'okf_version:[ \t]*["\']?0\.1["\']?', 'okf_version: "0.2"', text
            )
        if "okf_version" not in text:
            return text.replace("---\n", '---\nokf_version: "0.2"\n', 1)
        return text

    return f'---\nokf_version: "0.2"\n---\n\n{text}'


def _migrate_concept(text: str) -> str:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return text

    raw_yaml, body = match.group(1), match.group(2)
    changed = False

    # 1. Hoist timestamp -> generated.at (§13.1)
    ts_match = _TIMESTAMP_RE.search(raw_yaml)
    if ts_match and "generated:" not in raw_yaml:
        ts_val = ts_match.group(1).strip().strip("\"'")
        replacement = f"generated:\n  by: process:okf-migrate\n  at: '{ts_val}'"
        raw_yaml = _TIMESTAMP_RE.sub(replacement, raw_yaml)
        changed = True

    # 2. Extract # Citations -> sources (§13.1)
    cit_match = _CITATIONS_SECTION_RE.search(body)
    if cit_match and "sources:" not in raw_yaml:
        cit_text = cit_match.group(1)
        sources_yaml_lines: list[str] = ["sources:"]
        count = 0
        for m in _MD_LINK_RE.finditer(cit_text):
            title, url = m.group(1), m.group(2)
            slug = (
                re.sub(r"[^a-zA-Z0-9_-]", "-", title.lower()).strip("-")
                or f"src-{count}"
            )
            sources_yaml_lines.append(f"  - id: {slug}")
            sources_yaml_lines.append(f"    resource: '{url}'")
            sources_yaml_lines.append(f"    title: '{title}'")
            count += 1

        if count > 0:
            raw_yaml = raw_yaml.rstrip() + "\n" + "\n".join(sources_yaml_lines) + "\n"
            body = _CITATIONS_SECTION_RE.sub("", body).rstrip() + "\n"
            changed = True

    if changed:
        return f"---\n{raw_yaml.strip()}\n---\n\n{body.lstrip()}"
    return text
