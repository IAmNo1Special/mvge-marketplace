"""End-to-end: real engine payload + real emit_chain, in this Rune's suite.

Spec §5.8: chain ordering — sections append in load (registration) order; dict
round-trip — an earlier rune returning a dict passes through ``emit_chain`` /
``create_sigil_data`` with the new optional fields intact. Uses the real
``BeforeMvgeStartData``, ``create_sigil_data``, and ``RuneRunner.emit_chain`` —
no simulations.

The peer handler is a local stand-in, not steering-bridge. This suite used to
reach into the sibling's directory on ``sys.path`` to instantiate it, which is
the one thing a Rune's own suite must never do: it made ``pytest
runes/selfmod-bridge/tests`` unrunnable standalone, and the only reason it ever
ran was the repo-root ini putting every sibling Rune on the path. What these
tests actually assert is ``RuneRunner``'s ordering contract, and the peer only
has to append a distinguishable section. Whether two *real* marketplace Runes
compose is a marketplace-tier fact, asserted in ``tests/test_cross_rune_integration.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mvgeos_runes import SigilHook
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import create_sigil_data
from selfmod_bridge_conftest import make_rune

#: Marker the peer appends, so ordering is readable from the prompt alone.
PEER_HEADING = "Peer Rune Section:"


def _peer_hook(payload: Any) -> Any:
    """A second Rune's BEFORE_MVGE_START handler, kept in this suite.

    Appends its own section to ``base_prompt`` in place and returns the
    payload, which is what a real rune hook does.
    """
    if isinstance(payload, dict):
        base = payload.get("base_prompt", "")
        payload["base_prompt"] = f"{base}\n\n{PEER_HEADING} peer"
    else:
        payload.base_prompt = f"{payload.base_prompt}\n\n{PEER_HEADING} peer"
    return payload


def _payload_dict(tmp_path: Path) -> dict[str, Any]:
    return {
        "base_prompt": "engine base",
        "spell_names": [],
        "config_dir": str(tmp_path / "agent-config"),
        "custom_prompt": "",
        "agent_name": "test-agent",
        "cwd": str(tmp_path),
        "runes_paths": [str(tmp_path / "runes")],
        "system_path": str(tmp_path / "agent-config" / "SYSTEM.md"),
        "spells_dir": str(tmp_path / "agent-config" / "spells"),
    }


@pytest.mark.asyncio
async def test_real_payload_section_lands_in_base_prompt(
    tmp_path: Path,
) -> None:
    """The real engine payload type round-trips through create_sigil_data
    and the hook appends the section to base_prompt."""
    rune, _api = make_rune(tmp_path)
    payload = create_sigil_data(SigilHook.BEFORE_MVGE_START, _payload_dict(tmp_path))
    assert type(payload).__name__ == "BeforeMvgeStartData"
    await rune._on_before_mvge_start(payload)
    assert payload.base_prompt.startswith("engine base")
    assert "Self-Modification & Customization:" in payload.base_prompt


@pytest.mark.asyncio
async def test_chain_order_with_peer_handler(tmp_path: Path) -> None:
    """Both handlers' sections append in handler registration order through
    the real RuneRunner.emit_chain."""
    selfmod_rune, _api = make_rune(tmp_path)

    runner = RuneRunner()
    runner.register_handler(SigilHook.BEFORE_MVGE_START, _peer_hook)
    runner.register_handler(SigilHook.BEFORE_MVGE_START, selfmod_rune._on_before_mvge_start)

    result = await runner.emit_chain(SigilHook.BEFORE_MVGE_START, _payload_dict(tmp_path))
    prompt = result.base_prompt
    assert "engine base" in prompt
    peer_idx = prompt.find(PEER_HEADING)
    selfmod_idx = prompt.find("Self-Modification & Customization:")
    assert peer_idx != -1, "peer section missing"
    assert selfmod_idx != -1, "selfmod section missing"
    assert peer_idx < selfmod_idx, "sections out of registration order"


@pytest.mark.asyncio
async def test_chain_order_reversed_registration(tmp_path: Path) -> None:
    """Registration order decides append order, not rune identity."""
    selfmod_rune, _api = make_rune(tmp_path)

    runner = RuneRunner()
    runner.register_handler(SigilHook.BEFORE_MVGE_START, selfmod_rune._on_before_mvge_start)
    runner.register_handler(SigilHook.BEFORE_MVGE_START, _peer_hook)

    result = await runner.emit_chain(SigilHook.BEFORE_MVGE_START, _payload_dict(tmp_path))
    prompt = result.base_prompt
    selfmod_idx = prompt.find("Self-Modification & Customization:")
    peer_idx = prompt.find(PEER_HEADING)
    assert selfmod_idx != -1 and peer_idx != -1
    assert selfmod_idx < peer_idx, "sections out of registration order"


@pytest.mark.asyncio
async def test_dict_round_trip_through_emit_chain(tmp_path: Path) -> None:
    """An earlier rune returning a plain dict passes through emit_chain
    un-normalized; the selfmod hook still reads it via dict fallback and
    appends its section."""
    selfmod_rune, _api = make_rune(tmp_path)

    async def earlier_rune(payload: Any) -> dict[str, Any]:
        assert isinstance(payload, dict) or hasattr(payload, "base_prompt")
        return _payload_dict(tmp_path)

    runner = RuneRunner()
    runner.register_handler(SigilHook.BEFORE_MVGE_START, earlier_rune)
    runner.register_handler(SigilHook.BEFORE_MVGE_START, selfmod_rune._on_before_mvge_start)

    result = await runner.emit_chain(SigilHook.BEFORE_MVGE_START, _payload_dict(tmp_path))
    # The earlier rune returned a dict, so the chain stayed a dict.
    assert isinstance(result, dict)
    assert "Self-Modification & Customization:" in result["base_prompt"]
