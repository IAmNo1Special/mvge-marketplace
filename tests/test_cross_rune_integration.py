"""Cross-Rune integration. The only tier in this repository that may import a
sibling Rune's package.

The rule everywhere else is absolute: a Rune may import engine packages, and
never another Rune. That is what makes each Rune independently installable into
``~/.agents/extensions/`` without dragging a sibling along -- so a Rune's own
standalone suite must not import one either. Adding a sibling to a Rune's
``pythonpath`` to make its suite pass is not a workaround, it is the erosion:
it makes the violation permanent, invisible, and it silently ties the suite to a
directory the Rune will not have when it is installed on its own.

Two facts need both Runes to state, and both previously lived inside a single
Rune's suite:

- session-search indexes Pi sessions (needs pi-codec's ``PiSessionCodec``).
- selfmod-bridge and steering-bridge sections chain in registration order
  (needs two real Rune hooks on one ``BEFORE_MVGE_START``).

Here, both ``pythonpath`` entries already exist in the repo-root ini, so neither
test needs a scoped ``sys.path`` insert. The unit-level version of the second
fact -- that ``RuneRunner`` honours registration order -- stays in
``runes/selfmod-bridge/tests/test_chain.py`` with a local peer handler.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mvgeos_runes import SigilHook
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes_pi_codec.codec import PiSessionCodec
from mvgeos_runes_selfmod_bridge.rune import SelfmodBridgeRune
from mvgeos_runes_selfmod_bridge.state import SelfmodState
from mvgeos_runes_session_search.db import get_db_connection, search_messages
from mvgeos_runes_session_search.indexer import sync_index
from mvgeos_runes_steering_bridge.rune import SteeringBridgeRune


def test_pi_session_indexed(tmp_path: Path) -> None:
    """session-search indexes a Pi session when given pi-codec's codec.

    Moved out of ``runes/session-search/tests/``, where it was gated behind
    ``pytest.importorskip``. The skip was the problem: it made a missing sibling
    indistinguishable from a passing test, and the sibling is not optional here
    -- it is a published artifact of this marketplace, always on the root
    ``pythonpath``. A hard import failure is the correct signal if that stops
    being true.
    """
    tome_dir = tmp_path / "sessions"
    tome_dir.mkdir()
    brain_dir = tmp_path / "brain"
    db_path = tmp_path / "search.db"

    header = {
        "kind": "header",
        "v": 4,
        "id": "pi-sess-1",
        "cwd": "/tmp/proj",
        "storageVersion": 1,
        "createdAt": 1750000000000,
    }
    lines = [
        json.dumps(header),
        json.dumps(
            {
                "kind": "entry",
                "id": "m1",
                "parentId": None,
                "seq": 1,
                "timestamp": 1750000001000,
                "type": "message",
                "message": {
                    "role": "user",
                    "content": "pi says hello sqlite",
                    "timestamp": 1750000001000,
                },
            }
        ),
        json.dumps(
            {
                "kind": "value",
                "op": "set",
                "seq": 2,
                "namespace": "pi.branch.tip",
                "key": "main",
                "value": "m1",
            }
        ),
    ]
    (tome_dir / "2026-09-20-pi.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    stats = sync_index(
        tome_dir=tome_dir,
        brain_dir=brain_dir,
        db_path=db_path,
        codecs=[PiSessionCodec()],
    )
    assert stats["indexed_new"] == 1

    conn = get_db_connection(db_path)
    try:
        results = search_messages(conn, "sqlite")
        assert len(results) == 1
        assert results[0]["source"] == "pi"
    finally:
        conn.close()


def _selfmod_rune(tmp_path: Path) -> SelfmodBridgeRune:
    """A real selfmod-bridge rune with hook-derived state populated."""
    config_dir = tmp_path / "agent-config"
    (config_dir / "spells").mkdir(parents=True)
    system_path = config_dir / "SYSTEM.md"
    system_path.write_text("# Test System\n\nYou are a test agent.\n", encoding="utf-8")
    runes_root = tmp_path / "runes"
    runes_root.mkdir(exist_ok=True)

    rune = SelfmodBridgeRune(_stub_api(), {"version": "0.1.0"})
    rune.state = SelfmodState(
        config_dir=config_dir,
        runes_paths=[runes_root],
        system_path=system_path,
        spells_dir=config_dir / "spells",
        agent_name="test-agent",
        cwd=tmp_path,
    )
    return rune


def _steering_rune(tmp_path: Path) -> SteeringBridgeRune:
    """A real steering-bridge rune that will emit a ``<project_context>`` section."""
    (tmp_path / "AGENTS.md").write_text(
        "# Workspace rules\n\nBe kind.\n", encoding="utf-8"
    )
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "AGENTS.md").write_text(
        "# Global\n\nGlobal standard.\n", encoding="utf-8"
    )

    api = _stub_api()
    api.context.cwd = str(tmp_path)
    api.context.agent_name = "test-agent"
    rune = SteeringBridgeRune(api)
    rune.refresh_steering(cwd=tmp_path, global_dir=global_dir)
    return rune


def _stub_api() -> Any:
    """A RuneAPI double: registration is not what these tests are about."""
    from unittest.mock import MagicMock

    return MagicMock()


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
@pytest.mark.parametrize("selfmod_first", [True, False])
async def test_two_real_runes_chain_in_registration_order(
    tmp_path: Path, selfmod_first: bool
) -> None:
    """Two real marketplace Runes on one Sigil, in both registration orders.

    The unit-level guarantee -- ``RuneRunner`` appends sections in registration
    order -- is asserted in ``runes/selfmod-bridge/tests/test_chain.py`` with a
    stand-in peer. What only this tier can see is that both Runes' real hooks
    are actually composable: neither clobbers the other's ``base_prompt``, and
    both sections survive to the end of the chain.
    """
    selfmod_rune = _selfmod_rune(tmp_path)
    steering_rune = _steering_rune(tmp_path)

    runner = RuneRunner()
    handlers = [selfmod_rune._on_before_mvge_start, steering_rune.on_before_mvge_start]
    if not selfmod_first:
        handlers.reverse()
    for handler in handlers:
        runner.register_handler(SigilHook.BEFORE_MVGE_START, handler)

    from mvgeos_runes.types import BeforeMvgeStartData

    payload = BeforeMvgeStartData(**_payload_dict(tmp_path))
    result = await runner.emit_chain(SigilHook.BEFORE_MVGE_START, payload)
    prompt = (
        result.base_prompt if hasattr(result, "base_prompt") else result["base_prompt"]
    )

    assert "engine base" in prompt
    steering_idx = prompt.find("Steering")
    selfmod_idx = prompt.find("Self-Modification & Customization:")
    assert steering_idx != -1, "steering section missing"
    assert selfmod_idx != -1, "selfmod section missing"
    if selfmod_first:
        assert selfmod_idx < steering_idx, "sections out of registration order"
    else:
        assert steering_idx < selfmod_idx, "sections out of registration order"
