"""OKF v0.2 concept parser and frontmatter extractor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mvge_runes_okf_bridge.types import (
    AttestedComputation,
    Concept,
    Source,
    UsageWindow,
)

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)(?:\r?\n)?---\r?\n?(.*)$", re.DOTALL)
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FOOTNOTE_RE = re.compile(r"\[\^([^\]\s]+)\]")


def extract_frontmatter_and_body(
    text: str,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Extract YAML frontmatter and body from markdown text.

    Returns:
        (frontmatter_dict, body, error_message)
    """
    if not text.startswith("---"):
        return None, text, "Missing frontmatter block (file must start with '---')"

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text, "Unclosed frontmatter block (missing closing '---')"

    raw_yaml, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw_yaml)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, body, "Frontmatter must be a YAML mapping"
        return data, body, None
    except yaml.YAMLError as exc:
        return None, body, f"YAML parsing error: {exc}"


def parse_concept_file(
    path: Path, bundle_root: Path
) -> tuple[Concept | None, str | None]:
    """Parse an OKF concept file into a Concept dataclass.

    Returns:
        (Concept, None) on success, or (None, error_message) on failure.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"Could not read file as UTF-8: {exc}"

    data, body, err = extract_frontmatter_and_body(text)
    if err or data is None:
        return None, err or "Invalid frontmatter"

    # §11: type is required and must be non-empty
    raw_type = data.get("type")
    if not raw_type or not str(raw_type).strip():
        return None, "Missing or empty required field 'type'"

    concept_type = str(raw_type).strip()

    # Relative path minus .md becomes concept id
    try:
        rel_path = path.relative_to(bundle_root)
        concept_id = rel_path.as_posix()
        concept_id = concept_id.removesuffix(".md")
    except ValueError:
        concept_id = path.stem

    # Generated metadata (§5.2, §7)
    generated = data.get("generated")
    if isinstance(generated, dict):
        gen_dict = {str(k): str(v) for k, v in generated.items()}
    else:
        gen_dict = {}

    # Trust verified field (§5.2): bare dict is treated as 1-element list
    raw_verified = data.get("verified")
    verified_list: list[dict[str, str]] = []
    if isinstance(raw_verified, dict):
        verified_list.append({str(k): str(v) for k, v in raw_verified.items()})
    elif isinstance(raw_verified, list):
        for item in raw_verified:
            if isinstance(item, dict):
                verified_list.append({str(k): str(v) for k, v in item.items()})

    # Provenance sources (§5.1)
    sources: list[Source] = []
    raw_sources = data.get("sources")
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if isinstance(s, dict) and "id" in s and "resource" in s:
                sources.append(
                    Source(
                        id=str(s["id"]),
                        resource=str(s["resource"]),
                        title=str(s.get("title", "")),
                        author=str(s.get("author", "")),
                        usage_count=int(s["usage_count"])
                        if "usage_count" in s and s["usage_count"] is not None
                        else None,
                        last_modified=str(s.get("last_modified", "")),
                    )
                )

    # Usage window
    raw_window = data.get("usage_window")
    usage_window = None
    if isinstance(raw_window, dict):
        usage_window = UsageWindow(
            from_date=str(raw_window.get("from", "")),
            to_date=str(raw_window.get("to", "")),
        )

    # Attested computation (§10)
    computation = None
    if (
        concept_type == "Attested Computation"
        or "computation" in data
        or "runtime" in data
    ):
        computation = AttestedComputation(
            runtime=str(data.get("runtime", "")),
            parameters=data.get("parameters", {})
            if isinstance(data.get("parameters"), dict)
            else {},
            executor=data.get("executor", {})
            if isinstance(data.get("executor"), dict)
            else {},
            attester=data.get("attester", {})
            if isinstance(data.get("attester"), dict)
            else {},
            computation=str(data.get("computation", "")),
        )

    # Cross-links and footnotes
    links: list[str] = []
    for link_target in _LINK_RE.findall(body):
        resolved = _resolve_concept_link(link_target, path.parent, bundle_root)
        if resolved and resolved != concept_id:
            links.append(resolved)

    footnotes = _FOOTNOTE_RE.findall(body)

    # Tags normalization
    raw_tags = data.get("tags")
    tags: list[str] = []
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if t is not None]

    # Runtime-injection hint (dotagents extension key, OKF §4.1 allows it)
    raw_context = data.get("context", "")
    context = str(raw_context).strip() if raw_context else ""

    return Concept(
        id=concept_id,
        path=path,
        type=concept_type,
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        resource=str(data.get("resource", "")),
        tags=tags,
        status=str(data.get("status", "stable")),
        stale_after=str(data["stale_after"])
        if "stale_after" in data and data["stale_after"] is not None
        else None,
        context=context,
        generated=gen_dict,
        verified=verified_list,
        sources=sources,
        usage_window=usage_window,
        computation=computation,
        body=body,
        links=links,
        footnotes=footnotes,
    ), None


def _resolve_concept_link(
    target: str, current_dir: Path, bundle_root: Path
) -> str | None:
    """Resolve a markdown link target to a concept ID if within bundle."""
    # Ignore external URLs and anchors
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None

    # Strip anchors / query params
    clean_target = target.split("#")[0].split("?")[0]
    if not clean_target:
        return None

    try:
        if clean_target.startswith("/"):
            # Bundle-root relative: /services/auth-api.md
            target_path = bundle_root / clean_target.lstrip("/")
        else:
            # File relative: ../services/auth-api.md
            target_path = (current_dir / clean_target).resolve()

        if target_path.suffix == ".md":
            rel = target_path.relative_to(bundle_root)
            cid = rel.as_posix()
            return cid.removesuffix(".md")
    except (ValueError, OSError):
        return None

    return None
