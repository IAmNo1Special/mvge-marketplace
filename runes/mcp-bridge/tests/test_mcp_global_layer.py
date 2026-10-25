"""The user-scope mcp.json must be found under the relocated global layer.

``get_prioritized_mcp_configs`` honoured a ``global_dir`` *argument* but fell
back to ``Path.home()`` when it was omitted, so the user layer was read from
the real home directory even under ``$MVGE_GLOBAL_DIR``.

There is a second trap here that the obvious one-line fix walks into.
``global_agents_dir()`` already *is* the ``.agents`` layer, so joining
another ``.agents`` onto it would look under ``<layer>/.agents/mcp.json`` and
silently find nothing -- the install path and the discovery path resolving
differently, which is the failure mode the shared resolver exists to
prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge.core import GLOBAL_DIR_ENV, global_agents_dir
from mvge_runes_mcp_bridge.config import (
    _global_mcp_config_path,
    get_prioritized_mcp_configs,
)


@pytest.fixture
def relocated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``.agents`` layer at a temp root for one test."""
    # Deliberately local rather than shared from the repository root:
    # each rune is an independently installable, independently testable
    # package (its own pyproject.toml declares its own testpaths), and a
    # suite run from inside the rune directory never loads the root
    # conftest.py.
    root = tmp_path / "global"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(root))
    return root


def _write_mcp_config(directory: Path, server: str, command: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mcp.json").write_text(
        json.dumps({"mcpServers": {server: {"command": command}}}),
        encoding="utf-8",
    )


def test_user_config_is_read_from_the_relocated_layer(
    relocated_global: Path,
) -> None:
    """Omitting global_dir must not fall back to the real home directory."""
    _write_mcp_config(relocated_global, "relocated", "relocated_cmd")

    configs = get_prioritized_mcp_configs(cwd=None, global_dir=None)

    assert "relocated" in configs
    assert configs["relocated"].command == "relocated_cmd"


def test_user_config_is_not_looked_for_under_a_nested_agents_dir(
    relocated_global: Path,
) -> None:
    """<layer>/mcp.json is the location, never <layer>/.agents/mcp.json.

    The layer root is already ``.agents``; joining it a second time is the
    double-count that would make a relocated global dir read as empty.
    """
    nested = relocated_global / ".agents"
    _write_mcp_config(nested, "nested", "nested_cmd")

    configs = get_prioritized_mcp_configs(cwd=None, global_dir=None)

    assert "nested" not in configs, (
        "found the config at <layer>/.agents/mcp.json, which double-counts the "
        "layer; the correct location is <layer>/mcp.json"
    )


def test_explicit_base_containing_a_layer_still_works(tmp_path: Path) -> None:
    """An explicit base directory keeps both spellings working.

    Callers may pass a base that contains the layer, or the layer itself;
    both were accepted before and must stay accepted.
    """
    base = tmp_path / "base"
    _write_mcp_config(base / ".agents", "via_base", "base_cmd")

    configs = get_prioritized_mcp_configs(cwd=None, global_dir=base)
    assert configs["via_base"].command == "base_cmd"


def test_explicit_layer_directory_still_works(tmp_path: Path) -> None:
    """An explicit directory that *is* the layer keeps working."""
    layer = tmp_path / "base" / ".agents"
    _write_mcp_config(layer, "via_layer", "layer_cmd")

    configs = get_prioritized_mcp_configs(cwd=None, global_dir=layer)
    assert configs["via_layer"].command == "layer_cmd"


def test_explicit_base_does_not_widen_to_a_bare_mcp_json(
    tmp_path: Path,
) -> None:
    """An explicit base keeps its original lookup; nothing new is consulted.

    Guarding against scope creep in the fix itself: the change is which layer
    is authoritative, not which paths a caller-supplied directory may reach.
    """
    base = tmp_path / "base"
    base.mkdir()
    _write_mcp_config(base, "bare", "bare_cmd")

    assert get_prioritized_mcp_configs(cwd=None, global_dir=base) == {}


def test_default_location_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override the lookup agrees with the engine's own resolver."""
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)
    assert global_agents_dir() == Path("~/.agents").expanduser()
    assert _global_mcp_config_path(None) == (
        Path("~/.agents").expanduser() / "mcp.json"
    )


def test_project_layer_still_overrides_the_user_layer(
    relocated_global: Path, tmp_path: Path
) -> None:
    """Project config keeps priority over user config for a shared name."""
    _write_mcp_config(relocated_global, "shared", "user_cmd")

    project_agents = tmp_path / "project" / ".agents"
    _write_mcp_config(project_agents, "shared", "project_cmd")

    configs = get_prioritized_mcp_configs(cwd=tmp_path / "project", global_dir=None)
    assert configs["shared"].command == "project_cmd"
