"""Configuration loading and environment expansion for MCP Bridge."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from mvge.core import global_agents_dir

try:
    from .types import (
        MCPDiagnostic,
        MCPDiagnosticKind,
        MCPServerConfig,
        MCPTransport,
    )
except ImportError:
    from mvgeos_runes_mcp_bridge.types import (
        MCPDiagnostic,
        MCPDiagnosticKind,
        MCPServerConfig,
        MCPTransport,
    )

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(val: str, env: dict[str, str] | None = None) -> str:
    """Expands ${VAR} syntax in a string.

    Expands ${VAR} syntax using regex \\${([^}]+)}. Falls back to os.environ
    if env is None.

    Args:
        val: The string containing environment variable references.
        env: Optional mapping of environment variable names to values.

    Returns:
        The string with expanded environment variables.
    """
    lookup: Mapping[str, str] = os.environ if env is None else env
    return _ENV_VAR_PATTERN.sub(
        lambda m: lookup.get(m.group(1), lookup.get(m.group(1).strip(), "")),
        val,
    )


def _parse_mcp_json(
    path: Path,
    diagnostics: list[MCPDiagnostic] | None = None,
) -> dict[str, MCPServerConfig]:
    """Reads and parses a single mcp.json file.

    Expands env vars in command, args, env values, and url. On parse errors,
    appends MCPDiagnostic with INVALID_CONFIG.

    Args:
        path: Path to the mcp.json file to parse.
        diagnostics: Optional list to append diagnostics to on errors.

    Returns:
        Dict mapping server name to MCPServerConfig.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        if diagnostics is not None:
            diagnostics.append(
                MCPDiagnostic(
                    kind=MCPDiagnosticKind.INVALID_CONFIG,
                    server_name="",
                    message=f"Failed to read file: {exc}",
                    path=str(path),
                )
            )
        return {}

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        if diagnostics is not None:
            diagnostics.append(
                MCPDiagnostic(
                    kind=MCPDiagnosticKind.INVALID_CONFIG,
                    server_name="",
                    message=f"JSON parse error: {exc}",
                    path=str(path),
                )
            )
        return {}

    if not isinstance(data, dict):
        if diagnostics is not None:
            diagnostics.append(
                MCPDiagnostic(
                    kind=MCPDiagnosticKind.INVALID_CONFIG,
                    server_name="",
                    message="Configuration root must be a JSON object",
                    path=str(path),
                )
            )
        return {}

    servers_raw = data.get("mcpServers")
    if servers_raw is None:
        if diagnostics is not None:
            diagnostics.append(
                MCPDiagnostic(
                    kind=MCPDiagnosticKind.INVALID_CONFIG,
                    server_name="",
                    message="Configuration missing 'mcpServers' key",
                    path=str(path),
                )
            )
        return {}

    if not isinstance(servers_raw, dict):
        if diagnostics is not None:
            diagnostics.append(
                MCPDiagnostic(
                    kind=MCPDiagnosticKind.INVALID_CONFIG,
                    server_name="",
                    message="'mcpServers' must be a JSON object",
                    path=str(path),
                )
            )
        return {}

    configs: dict[str, MCPServerConfig] = {}
    for server_name, server_data in servers_raw.items():
        name_str = str(server_name)
        if not isinstance(server_data, dict):
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.INVALID_CONFIG,
                        server_name=name_str,
                        message="Server config must be a JSON object",
                        path=str(path),
                    )
                )
            continue

        raw_transport = server_data.get("transport")
        if raw_transport is not None:
            try:
                transport = MCPTransport(raw_transport)
            except ValueError:
                if diagnostics is not None:
                    diagnostics.append(
                        MCPDiagnostic(
                            kind=MCPDiagnosticKind.INVALID_CONFIG,
                            server_name=name_str,
                            message=f"Invalid transport: {raw_transport}",
                            path=str(path),
                        )
                    )
                continue
        elif "url" in server_data and "command" not in server_data:
            transport = MCPTransport.SSE
        else:
            transport = MCPTransport.STDIO

        raw_env = server_data.get("env", {})
        if not isinstance(raw_env, dict):
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.INVALID_CONFIG,
                        server_name=name_str,
                        message="'env' must be an object",
                        path=str(path),
                    )
                )
            continue

        expanded_env: dict[str, str] = {
            str(k): expand_env_vars(str(v)) for k, v in raw_env.items()
        }
        lookup_env = {**os.environ, **expanded_env}

        raw_command = server_data.get("command", "")
        if not isinstance(raw_command, str):
            raw_command = str(raw_command)
        command = expand_env_vars(raw_command, lookup_env) if raw_command else ""

        raw_args = server_data.get("args", [])
        if not isinstance(raw_args, list):
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.INVALID_CONFIG,
                        server_name=name_str,
                        message="'args' must be a list",
                        path=str(path),
                    )
                )
            continue
        args = [expand_env_vars(str(arg), lookup_env) for arg in raw_args]

        raw_url = server_data.get("url", "")
        if not isinstance(raw_url, str):
            raw_url = str(raw_url)
        url = expand_env_vars(raw_url, lookup_env) if raw_url else ""

        raw_cwd = server_data.get("cwd")
        cwd: str | None = None
        if raw_cwd is not None:
            cwd = expand_env_vars(str(raw_cwd), lookup_env)

        raw_headers = server_data.get("headers", {})
        headers: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            headers = {
                str(k): expand_env_vars(str(v), lookup_env)
                for k, v in raw_headers.items()
            }
        elif raw_headers:
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.INVALID_CONFIG,
                        server_name=name_str,
                        message="'headers' must be an object",
                        path=str(path),
                    )
                )
            continue

        raw_timeout = server_data.get("timeout", 30.0)
        try:
            timeout = float(raw_timeout)
        except (ValueError, TypeError):
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.INVALID_CONFIG,
                        server_name=name_str,
                        message=f"Invalid timeout: {raw_timeout}",
                        path=str(path),
                    )
                )
            continue

        disabled = bool(server_data.get("disabled", False))

        configs[name_str] = MCPServerConfig(
            name=name_str,
            transport=transport,
            command=command,
            args=args,
            env=expanded_env,
            cwd=cwd,
            url=url,
            headers=headers,
            timeout=timeout,
            disabled=disabled,
        )

    return configs


def _global_mcp_config_path(global_dir: Path | None) -> Path:
    """The user-scope ``mcp.json``, inside the global ``.agents`` layer.

    Resolved through ``mvge.core.global_agents_dir`` rather than spelled
    out from ``Path.home()`` so a relocated global layer
    (``$MVGEOS_GLOBAL_DIR``) is honoured by callers that omit the argument
    -- which is every production caller.

    Note the layer root *is* ``.agents``, so with no explicit directory the
    file is ``<layer>/mcp.json``. Joining a second ``.agents`` onto it would
    look under ``<layer>/.agents/mcp.json``, find nothing, and report an
    empty configuration while the file sits right there.

    An explicit ``global_dir`` keeps the exact lookup it has always had: a
    base containing the layer, or the layer itself (recognised by name).
    Widening that to any directory would change which paths are consulted,
    which is a separate decision from which layer is authoritative.
    """
    if global_dir is None:
        return global_agents_dir() / "mcp.json"
    nested = global_dir / ".agents" / "mcp.json"
    if nested.is_file():
        return nested
    if global_dir.name == ".agents":
        return global_dir / "mcp.json"
    return nested


def get_prioritized_mcp_configs(
    cwd: Path | None = None,
    global_dir: Path | None = None,
    diagnostics: list[MCPDiagnostic] | None = None,
) -> dict[str, MCPServerConfig]:
    """Loads USER and PROJECT MCP configurations with precedence.

    Loads USER config from the global ``.agents`` layer (default
    ``$MVGEOS_GLOBAL_DIR`` or ``~/.agents``), then PROJECT config from
    ``cwd/.agents/mcp.json``. PROJECT overrides USER (same server name).
    Disabled servers are excluded.

    Args:
        cwd: Project root directory containing .agents/mcp.json. Defaults to
            Path.cwd().
        global_dir: Global agents directory, or a base containing one.
            Defaults to the resolved global layer.
        diagnostics: Optional list to append diagnostics to on parse errors.

    Returns:
        Dict mapping active server names to their MCPServerConfig.
    """
    user_path = _global_mcp_config_path(global_dir)

    user_configs: dict[str, MCPServerConfig] = {}
    if user_path.is_file():
        user_configs = _parse_mcp_json(user_path, diagnostics)

    project_base = cwd if cwd is not None else Path.cwd()
    if (project_base / ".agents" / "mcp.json").is_file():
        project_path = project_base / ".agents" / "mcp.json"
    elif (project_base / "mcp.json").is_file() and project_base.name == ".agents":
        project_path = project_base / "mcp.json"
    else:
        project_path = project_base / ".agents" / "mcp.json"

    project_configs: dict[str, MCPServerConfig] = {}
    if project_path.is_file():
        try:
            same_file = project_path.resolve() == user_path.resolve()
        except OSError:
            same_file = False
        if not same_file:
            project_configs = _parse_mcp_json(project_path, diagnostics)

    merged = dict(user_configs)
    merged.update(project_configs)

    return {name: cfg for name, cfg in merged.items() if not cfg.disabled}


__all__ = [
    "_parse_mcp_json",
    "expand_env_vars",
    "get_prioritized_mcp_configs",
]
