from __future__ import annotations

import json
from pathlib import Path

from mvge_runes_opentelemetry_bridge.config import load_otel_config
from mvge_runes_opentelemetry_bridge.types import OTelConfig


def test_load_otel_config_from_files(tmp_path: Path, monkeypatch) -> None:
    # Clear any environment overrides
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("MVGE_OTEL_IN_MEMORY", raising=False)

    global_dir = tmp_path / "global_agents"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "otel.json").write_text(
        json.dumps(
            {
                "service_name": "global-service",
                "endpoint": "http://global:4318",
                "in_memory": False,
            }
        ),
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    (project_dir / ".agents").mkdir(parents=True, exist_ok=True)
    (project_dir / ".agents" / "otel.json").write_text(
        json.dumps(
            {
                "service_name": "project-service",
                "in_memory": True,
                "headers": {"X-Custom": "123"},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_otel_config(cwd=project_dir, global_dir=global_dir)
    assert cfg.service_name == "project-service"
    assert cfg.endpoint == "http://global:4318"
    assert cfg.in_memory is True
    assert cfg.headers == {"X-Custom": "123"}


def test_load_otel_config_env_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "env-service")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://env:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("MVGE_OTEL_IN_MEMORY", "1")

    cfg = load_otel_config(cwd=tmp_path)
    assert cfg.service_name == "env-service"
    assert cfg.endpoint == "http://env:4318"
    assert cfg.disabled is True
    assert cfg.in_memory is True


def test_load_otel_config_invalid_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    global_dir = tmp_path / "global_corrupt"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "otel.json").write_text("{not valid json", encoding="utf-8")

    project_dir = tmp_path / "proj_corrupt"
    (project_dir / ".agents").mkdir(parents=True, exist_ok=True)
    (project_dir / ".agents" / "otel.json").write_text(
        "{also invalid", encoding="utf-8"
    )

    cfg = load_otel_config(cwd=project_dir, global_dir=global_dir)
    assert cfg.service_name == "mvge"
    assert cfg.disabled is False


def test_apply_dict_config_all_fields() -> None:
    from mvge_runes_opentelemetry_bridge.config import _apply_dict_config

    cfg = OTelConfig()
    _apply_dict_config(
        cfg,
        {
            "service_name": "custom",
            "endpoint": "http://custom:4318",
            "headers": {"A": "B"},
            "in_memory": True,
            "disabled": True,
        },
    )
    assert cfg.service_name == "custom"
    assert cfg.endpoint == "http://custom:4318"
    assert cfg.headers == {"A": "B"}
    assert cfg.in_memory is True
    assert cfg.disabled is True
