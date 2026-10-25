"""Prompt rendering and in-place context transformation for architectural decisions."""

from __future__ import annotations

import re
from typing import Any, cast

from mvge_runes_adr_bridge.types import ADREntry

_ADR_BLOCK_RE = re.compile(
    r"<architectural_decisions.*?</architectural_decisions>", re.DOTALL
)


def render_adr_catalog(adrs: list[ADREntry], adr_path: str = "docs/adr/") -> str:
    """Render a compact <architectural_decisions> XML block.

    Returns an empty string if no ADRs are available (silent bypass).
    """
    if not adrs:
        return ""

    lines: list[str] = [
        f'<architectural_decisions path="{adr_path}" count="{len(adrs)}">'
    ]

    for a in adrs:
        date_attr = f' date="{a.date}"' if a.date else ""
        lines.append(
            f'  <adr number="{a.number:04d}" title="{a.title}" status="{a.status}"{date_attr} />'
        )

    lines.append("  <instructions>")
    lines.append(
        "    The project maintains Architectural Decision Records (MADR 3.0) in docs/adr/.\n"
        "    To inspect complete decision outcomes and options, use the 'adr_get' spell.\n"
        "    To list decisions, use 'adr_list'. To scaffold a new decision, use 'adr_new'."
    )
    lines.append("  </instructions>")
    lines.append("</architectural_decisions>")

    return "\n".join(lines)


def update_invocations_with_adr_catalog(
    invocations: list[Any], adr_xml: str
) -> list[Any]:
    """In-place replacement of <architectural_decisions> block to prevent multi-turn token accumulation."""
    if not invocations:
        return invocations

    result = list(invocations)
    replaced = False

    for i, inv in enumerate(result):
        text = ""
        if hasattr(inv, "text"):
            text = getattr(inv, "text", "") or ""
        elif isinstance(inv, dict) and "content" in inv:
            text = str(inv["content"])

        if _ADR_BLOCK_RE.search(text):
            if adr_xml:
                new_text = _ADR_BLOCK_RE.sub(adr_xml, text)
            else:
                new_text = _ADR_BLOCK_RE.sub("", text).strip()

            if hasattr(inv, "text"):
                from dataclasses import is_dataclass, replace

                if is_dataclass(inv):
                    result[i] = replace(cast(Any, inv), text=new_text)
                else:
                    inv.text = new_text
            elif isinstance(inv, dict):
                result[i] = dict(inv, content=new_text)
            replaced = True
            break

    if not replaced and adr_xml:
        first = result[0]
        if hasattr(first, "text"):
            old_t = getattr(first, "text", "") or ""
            new_t = f"{old_t}\n\n{adr_xml}".strip()
            from dataclasses import is_dataclass, replace

            if is_dataclass(first):
                result[0] = replace(cast(Any, first), text=new_t)
            else:
                first.text = new_t
        elif isinstance(first, dict) and "content" in first:
            old_c = str(first["content"])
            result[0] = dict(first, content=f"{old_c}\n\n{adr_xml}".strip())

    return result
