from __future__ import annotations

import json
from pathlib import Path

from mvge_runes_mcp_bridge.config import (
    _parse_mcp_json,
    expand_env_vars,
    get_prioritized_mcp_configs,
)
from mvge_runes_mcp_bridge.types import (
    MCPDiagnostic,
    MCPDiagnosticKind,
    MCPTransport,
)


def test_expand_env_vars_custom_env() -> None:
    text = "Hello ${USER_NAME}, welcome to ${HOST_NAME}!"
    env = {"USER_NAME": "Alice", "HOST_NAME": "Wonderland"}
    res = expand_env_vars(text, env=env)
    assert res == "Hello Alice, welcome to Wonderland!"


def test_expand_env_vars_missing_env() -> None:
    text = "Key: ${SECRET_KEY}"
    res = expand_env_vars(text, env={})
    assert res == "Key: "


def test_parse_mcp_json_valid_stdio(tmp_path: Path) -> None:
    cfg_file = tmp_path / "mcp.json"
    data = {
        "mcpServers": {
            "test_server": {
                "command": "python",
                "args": ["-m", "server"],
                "env": {"DEBUG": "true"},
            }
        }
    }
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    diagnostics: list[MCPDiagnostic] = []
    configs = _parse_mcp_json(cfg_file, diagnostics=diagnostics)
    assert len(diagnostics) == 0
    assert "test_server" in configs
    cfg = configs["test_server"]
    assert cfg.name == "test_server"
    assert cfg.transport == MCPTransport.STDIO
    assert cfg.command == "python"
    assert cfg.args == ["-m", "server"]
    assert cfg.env == {"DEBUG": "true"}


def test_parse_mcp_json_env_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_CMD", "npx")
    monkeypatch.setenv("API_URL", "http://localhost:8080/sse")
    cfg_file = tmp_path / "mcp.json"
    data = {
        "mcpServers": {
            "srv": {
                "command": "${SERVER_CMD}",
                "url": "${API_URL}",
                "transport": "sse",
            }
        }
    }
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    configs = _parse_mcp_json(cfg_file)
    assert "srv" in configs
    assert configs["srv"].command == "npx"
    assert configs["srv"].url == "http://localhost:8080/sse"
    assert configs["srv"].transport == MCPTransport.SSE


def test_parse_mcp_json_invalid_json(tmp_path: Path) -> None:
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text("{invalid json", encoding="utf-8")

    diagnostics: list[MCPDiagnostic] = []
    configs = _parse_mcp_json(cfg_file, diagnostics=diagnostics)
    assert configs == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == MCPDiagnosticKind.INVALID_CONFIG


def test_parse_mcp_json_non_dict_root(tmp_path: Path) -> None:
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text('["item"]', encoding="utf-8")

    diagnostics: list[MCPDiagnostic] = []
    configs = _parse_mcp_json(cfg_file, diagnostics=diagnostics)
    assert configs == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == MCPDiagnosticKind.INVALID_CONFIG


def test_parse_mcp_json_missing_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "nonexistent.json"
    diagnostics: list[MCPDiagnostic] = []
    configs = _parse_mcp_json(cfg_file, diagnostics=diagnostics)
    assert configs == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == MCPDiagnosticKind.INVALID_CONFIG


def test_get_prioritized_mcp_configs(tmp_path: Path) -> None:
    # 1. User config in global_dir/.agents/mcp.json
    global_dir = tmp_path / "global"
    global_agents = global_dir / ".agents"
    global_agents.mkdir(parents=True)
    user_data = {
        "mcpServers": {
            "shared": {
                "command": "global_cmd",
                "args": [],
            },
            "user_only": {
                "command": "user_cmd",
                "args": [],
            },
            "disabled_server": {
                "command": "disabled_cmd",
                "disabled": True,
            },
        }
    }
    (global_agents / "mcp.json").write_text(json.dumps(user_data), encoding="utf-8")

    # 2. Project config in cwd/.agents/mcp.json
    project_dir = tmp_path / "project"
    project_agents = project_dir / ".agents"
    project_agents.mkdir(parents=True)
    proj_data = {
        "mcpServers": {
            "shared": {
                "command": "project_cmd_override",
                "args": ["--override"],
            },
            "project_only": {
                "command": "project_cmd",
                "args": [],
            },
        }
    }
    (project_agents / "mcp.json").write_text(json.dumps(proj_data), encoding="utf-8")

    diagnostics: list[MCPDiagnostic] = []
    configs = get_prioritized_mcp_configs(
        cwd=project_dir,
        global_dir=global_dir,
        diagnostics=diagnostics,
    )

    assert len(diagnostics) == 0
    # Project overrides user
    assert configs["shared"].command == "project_cmd_override"
    assert configs["shared"].args == ["--override"]
    # User-only present
    assert "user_only" in configs
    # Project-only present
    assert "project_only" in configs
    # Disabled server excluded
    assert "disabled_server" not in configs


def test_parse_mcp_json_validation_errors(tmp_path: Path) -> None:
    # 1. Missing mcpServers
    f1 = tmp_path / "f1.json"
    f1.write_text(json.dumps({"other": {}}), encoding="utf-8")
    d1: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f1, d1) == {}
    assert len(d1) == 1
    assert "missing 'mcpServers'" in d1[0].message

    # 2. mcpServers is not a dict
    f2 = tmp_path / "f2.json"
    f2.write_text(json.dumps({"mcpServers": "not_a_dict"}), encoding="utf-8")
    d2: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f2, d2) == {}
    assert len(d2) == 1
    assert "'mcpServers' must be a JSON object" in d2[0].message

    # 3. Server data is not a dict
    f3 = tmp_path / "f3.json"
    f3.write_text(json.dumps({"mcpServers": {"s1": 123}}), encoding="utf-8")
    d3: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f3, d3) == {}
    assert len(d3) == 1
    assert "Server config must be a JSON object" in d3[0].message

    # 4. Invalid transport
    f4 = tmp_path / "f4.json"
    f4.write_text(
        json.dumps({"mcpServers": {"s2": {"transport": "unknown"}}}),
        encoding="utf-8",
    )
    d4: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f4, d4) == {}
    assert len(d4) == 1
    assert "Invalid transport" in d4[0].message

    # 5. Invalid env (not dict)
    f5 = tmp_path / "f5.json"
    f5.write_text(
        json.dumps({"mcpServers": {"s3": {"env": "not_dict"}}}),
        encoding="utf-8",
    )
    d5: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f5, d5) == {}
    assert len(d5) == 1
    assert "'env' must be an object" in d5[0].message

    # 6. Invalid args (not list)
    f6 = tmp_path / "f6.json"
    f6.write_text(
        json.dumps({"mcpServers": {"s4": {"args": "not_list"}}}),
        encoding="utf-8",
    )
    d6: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f6, d6) == {}
    assert len(d6) == 1
    assert "'args' must be a list" in d6[0].message

    # 7. Invalid headers (not dict)
    f7 = tmp_path / "f7.json"
    f7.write_text(
        json.dumps({"mcpServers": {"s5": {"headers": "not_dict"}}}),
        encoding="utf-8",
    )
    d7: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f7, d7) == {}
    assert len(d7) == 1
    assert "'headers' must be an object" in d7[0].message

    # 8. Invalid timeout (not float)
    f8 = tmp_path / "f8.json"
    f8.write_text(
        json.dumps({"mcpServers": {"s6": {"timeout": "invalid_number"}}}),
        encoding="utf-8",
    )
    d8: list[MCPDiagnostic] = []
    assert _parse_mcp_json(f8, d8) == {}
    assert len(d8) == 1
    assert "Invalid timeout" in d8[0].message


def test_get_prioritized_mcp_configs_direct_dotagents_dir(tmp_path: Path) -> None:
    # When global_dir itself is named .agents
    user_agents = tmp_path / "user" / ".agents"
    user_agents.mkdir(parents=True)
    (user_agents / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "echo"}}}),
        encoding="utf-8",
    )

    proj_agents = tmp_path / "proj" / ".agents"
    proj_agents.mkdir(parents=True)
    (proj_agents / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "echo_override"}}}),
        encoding="utf-8",
    )

    configs = get_prioritized_mcp_configs(cwd=proj_agents, global_dir=user_agents)
    assert configs["srv"].command == "echo_override"


def test_parse_mcp_json_url_default_and_types(tmp_path: Path) -> None:
    cfg_file = tmp_path / "mcp_types.json"
    data = {
        "mcpServers": {
            "url_only": {
                "url": "http://localhost:8000/sse",
                "cwd": "/tmp/dir",
                "headers": {"X-Custom": "val"},
            },
            "non_str_fields": {
                "command": 12345,
                "url": 67890,
            },
        }
    }
    cfg_file.write_text(json.dumps(data), encoding="utf-8")
    configs = _parse_mcp_json(cfg_file)
    assert configs["url_only"].transport == MCPTransport.SSE
    assert configs["url_only"].cwd == "/tmp/dir"
    assert configs["url_only"].headers == {"X-Custom": "val"}
    assert configs["non_str_fields"].command == "12345"
    assert configs["non_str_fields"].url == "67890"
