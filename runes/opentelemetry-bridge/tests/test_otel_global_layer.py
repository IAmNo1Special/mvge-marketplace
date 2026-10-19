"""The global otel.json must be found under the relocated global layer.

``load_otel_config`` honoured a ``global_dir`` *argument* but fell back to
``Path.home() / ".agents"`` when it was omitted, so every production caller
(``rune.py``, which never passes one) read the real home directory even
under ``$MVGEOS_GLOBAL_DIR``. That defeats the isolation the engine's own
test suite sets up via ``_isolate_global_agents_dir``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvgeos_core import GLOBAL_DIR_ENV, global_agents_dir
from mvgeos_runes_opentelemetry_bridge.config import load_otel_config


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


_ENV_OVERRIDES = (
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SDK_DISABLED",
    "MVGEOS_OTEL_IN_MEMORY",
)


@pytest.fixture(autouse=True)
def _clear_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars win over files, so they must not leak into these tests."""
    for name in _ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)


def test_global_config_is_read_from_the_relocated_layer(
    relocated_global: Path,
) -> None:
    """Omitting global_dir must not fall back to the real home directory."""
    relocated_global.mkdir(parents=True)
    (relocated_global / "otel.json").write_text(
        json.dumps({"service_name": "relocated-service"}), encoding="utf-8"
    )

    cfg = load_otel_config(cwd=None, global_dir=None)
    assert cfg.service_name == "relocated-service"


def test_global_config_defaults_to_home_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override the resolver agrees with the engine's own."""
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)
    expected = global_agents_dir() / "otel.json"
    assert expected == Path("~/.agents").expanduser() / "otel.json"
    # Nothing to read there, so the config is simply left at its default.
    assert load_otel_config(cwd=None, global_dir=None).service_name == "mvgeos"


def test_explicit_global_dir_still_wins(relocated_global: Path, tmp_path: Path) -> None:
    """An explicit directory keeps priority over the resolved default.

    The fix changes the fallback, not the override, so callers that already
    pass a global_dir must be unaffected.
    """
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "otel.json").write_text(
        json.dumps({"service_name": "explicit-service"}), encoding="utf-8"
    )

    cfg = load_otel_config(cwd=None, global_dir=explicit)
    assert cfg.service_name == "explicit-service"


def test_project_layer_still_overrides_the_global_layer(
    relocated_global: Path, tmp_path: Path
) -> None:
    """The project .agents layer keeps priority over the global one."""
    relocated_global.mkdir(parents=True)
    (relocated_global / "otel.json").write_text(
        json.dumps(
            {
                "service_name": "relocated-service",
                "endpoint": "http://relocated",
            }
        ),
        encoding="utf-8",
    )

    project = tmp_path / "project"
    (project / ".agents").mkdir(parents=True)
    (project / ".agents" / "otel.json").write_text(
        json.dumps({"service_name": "project-service"}), encoding="utf-8"
    )

    cfg = load_otel_config(cwd=project, global_dir=None)
    assert cfg.service_name == "project-service"
    assert cfg.endpoint == "http://relocated"
