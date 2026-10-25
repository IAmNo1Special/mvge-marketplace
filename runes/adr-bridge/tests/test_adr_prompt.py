"""Unit tests for MADR 3.0 prompt injection and context transformation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mvge_runes_adr_bridge.prompt import (
    render_adr_catalog,
    update_invocations_with_adr_catalog,
)
from mvge_runes_adr_bridge.types import ADREntry


@dataclass
class DummyInvocation:
    text: str


def test_render_adr_catalog_empty() -> None:
    assert render_adr_catalog([]) == ""


def test_render_adr_catalog_with_entries() -> None:
    adrs = [
        ADREntry(
            number=1,
            title="Microkernel Architecture",
            status="accepted",
            date="2026-09-01",
            path=Path("0001-microkernel.md"),
        )
    ]
    xml = render_adr_catalog(adrs)
    assert "<architectural_decisions" in xml
    assert 'number="0001"' in xml
    assert 'title="Microkernel Architecture"' in xml
    assert 'status="accepted"' in xml
    assert "adr_get" in xml
    assert "</architectural_decisions>" in xml


def test_update_invocations_with_adr_catalog() -> None:
    invs = [DummyInvocation(text="System prompt:")]

    # 1. Append
    adr_v1 = "<architectural_decisions>V1</architectural_decisions>"
    updated = update_invocations_with_adr_catalog(invs, adr_v1)
    assert len(updated) == 1
    assert (
        "System prompt:\n\n<architectural_decisions>V1</architectural_decisions>"
        == updated[0].text
    )

    # 2. In-place replace
    adr_v2 = "<architectural_decisions>V2</architectural_decisions>"
    updated_v2 = update_invocations_with_adr_catalog(updated, adr_v2)
    assert len(updated_v2) == 1
    assert (
        "System prompt:\n\n<architectural_decisions>V2</architectural_decisions>"
        == updated_v2[0].text
    )
    assert "V1" not in updated_v2[0].text

    # 3. Clear
    cleared = update_invocations_with_adr_catalog(updated_v2, "")
    assert len(cleared) == 1
    assert cleared[0].text == "System prompt:"

    # 4. Dict format
    dict_invs = [{"content": "Prompt"}]
    dict_up = update_invocations_with_adr_catalog(
        dict_invs, "<architectural_decisions>ADR</architectural_decisions>"
    )
    assert (
        "<architectural_decisions>ADR</architectural_decisions>"
        in dict_up[0]["content"]
    )

    dict_rep = update_invocations_with_adr_catalog(
        dict_up, "<architectural_decisions>ADR2</architectural_decisions>"
    )
    assert (
        "<architectural_decisions>ADR2</architectural_decisions>"
        in dict_rep[0]["content"]
    )

    # 5. Empty
    assert update_invocations_with_adr_catalog([], "test") == []
