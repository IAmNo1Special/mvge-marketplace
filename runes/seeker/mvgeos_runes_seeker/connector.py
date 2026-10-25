from __future__ import annotations

import asyncio
import json
import os
from typing import Any, ClassVar

import aiohttp
from mvge.core.abort import AbortSignal
from mvge.core.spells import SpellContext
from mvge.runes.types import ExecutionMode, SpellDefinition

from .discovery import MCPServerInfo, MCPTransportError

MCP_PROTOCOL_VERSION = "2025-03-26"  # Negotiated with server

# Timeout defaults
_MCP_INIT_TIMEOUT = 15
_MCP_HTTP_TIMEOUT = 30
_MCP_STDIO_TIMEOUT = 30
_MCP_TOOL_LIST_TIMEOUT = 60  # pagination may take longer

# Safety limits
_MCP_MAX_TOOLS = 100
_MCP_MAX_RESOURCES = 100
_MCP_MAX_PROMPTS = 100
_MCP_MAX_PERMITTED_COMMANDS = [
    "npx",
    "uvx",
    "node",
    "python",
    "python3",
    "deno",
    "bun",
]


class MCPPermissionError(Exception):
    """Raised when MCP server config violates sandbox policy."""


_MCP_INJECTABLE_ENV_VARS: set[str] = {
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "NODE_PATH",
    "NODE_OPTIONS",
    "BASH_ENV",
    "ENV",
    "IFS",
    "PATHEXT",
    "PSModulePath",
}


def _check_sandbox(info: MCPServerInfo) -> None:
    """Sandbox/permission check before spawning any MCP server process.
    Prevents arbitrary command execution via config injection.
    Uses exact command name matching, validates args for shell metacharacters,
    and blocks known-injectable environment variables.
    """
    if info.transport == "stdio":
        cmd = info.config.get("command", "")
        if not cmd:
            raise MCPPermissionError("MCP server command is empty")
        bare_cmd = cmd.split()[0]
        if bare_cmd not in set(_MCP_MAX_PERMITTED_COMMANDS):
            raise MCPPermissionError(
                f"MCP server command '{bare_cmd}' not in permitted list: "
                f"{_MCP_MAX_PERMITTED_COMMANDS}"
            )
        # Validate args for shell metacharacters
        import re as _re

        _SHELL_META = _re.compile(r"[\"';|&$`(){}<>!#~*?\\]")
        for arg in info.config.get("args", []):
            if _SHELL_META.search(arg):
                raise MCPPermissionError(
                    f"MCP server arg '{arg}' contains shell metacharacters"
                )
        env = info.config.get("env", {})
        for key in env:
            if key in _MCP_INJECTABLE_ENV_VARS:
                raise MCPPermissionError(
                    f"Restricted env var '{key}' in MCP server config"
                )
    # HTTP transport: validate URL scheme (allow localhost for development)
    if info.transport == "streamable-http":
        url = info.config.get("url", "")
        if not url.startswith("https://"):
            from urllib.parse import urlparse

            parsed = urlparse(url)
            if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise MCPPermissionError(f"MCP HTTP URL must use HTTPS: {url}")


class MCPToolSpell(SpellDefinition):
    """
    SpellDefinition subclass wrapping an MCP tool.
    execute() sends tools/call JSON-RPC via the connector's send_jsonrpc().
    """

    def __init__(
        self,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        connector: MCPConnector,
    ) -> None:
        super().__init__(
            name=f"mcp_{tool_name}",
            description=description,
            parameters=input_schema,
            execution_mode=ExecutionMode.PARALLEL,
            prompt_guidelines=["This tool is provided by an MCP server"],
        )
        self._tool_name = tool_name
        self._connector = connector

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any]:
        result = await self._connector.send_jsonrpc(
            method="tools/call",
            params={"name": self._tool_name, "arguments": params},
            request_id=spell_cast_id or 1,
        )
        content = result.get("content", [])
        text = "".join(
            item.get("text", "") for item in content if item.get("type") == "text"
        )
        return {"content": text, "isError": result.get("isError", False)}


class MCPResourceSpell(SpellDefinition):
    """
    SpellDefinition wrapping an MCP resource for read-only data access.
    """

    def __init__(
        self,
        uri: str,
        name: str,
        description: str,
        mime_type: str,
        connector: MCPConnector,
    ) -> None:
        super().__init__(
            name=f"mcp_resource_{name}",
            description=description,
            parameters={
                "type": "object",
                "properties": {},
                "description": f"MCP resource: {uri} ({mime_type})",
            },
            execution_mode=ExecutionMode.PARALLEL,
        )
        self._uri = uri
        self._connector = connector

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any]:
        result = await self._connector.send_jsonrpc(
            method="resources/read",
            params={"uri": self._uri},
            request_id=spell_cast_id or 1,
        )
        contents = result.get("contents", [])
        text = "".join(c.get("text", "") for c in contents if c.get("type") == "text")
        return {"content": text, "mimeType": result.get("mimeType", "text/plain")}


class MCPPromptSpell(SpellDefinition):
    """
    SpellDefinition wrapping an MCP prompt template.
    """

    def __init__(
        self,
        prompt_name: str,
        description: str,
        arguments: list[dict[str, Any]],
        connector: MCPConnector,
    ) -> None:
        super().__init__(
            name=f"mcp_prompt_{prompt_name}",
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    arg["name"]: {
                        "type": "string",
                        "description": arg.get("description", ""),
                    }
                    for arg in arguments
                },
                "required": [arg["name"] for arg in arguments if arg.get("required")],
            },
            execution_mode=ExecutionMode.PARALLEL,
        )
        self._prompt_name = prompt_name
        self._connector = connector

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any]:
        result = await self._connector.send_jsonrpc(
            method="prompts/get",
            params={"name": self._prompt_name, "arguments": params},
            request_id=spell_cast_id or 1,
        )
        messages = result.get("messages", [])
        text = "".join(m.get("content", {}).get("text", "") for m in messages)
        return {"content": text}


class MCPConnector:
    """
    Manages an MCP server connection via stdio or Streamable HTTP.

    Security:
      - Sandbox check before spawning (permitted commands only, HTTPS-only for HTTP, arg validation)
      - Timeouts on all I/O operations
      - Pagination with limits on tools/resources/prompts
      - Shared aiohttp.ClientSession for HTTP transport
      - Stderr drained (DEVNULL) to prevent deadlock
      - Connector stored in _connections only after full initialization
      - Environment variable restrictions (injectable vars blocked)

    Lifecycle:
      connect()   -- sandbox check + transport connect + initialize
      register_all_capabilities()  -- paginated list + spell creation
      send_jsonrpc()        -- called by MCPToolSpell.execute()
      close()     -- session shutdown
    """

    def __init__(
        self,
        server_info: MCPServerInfo,
        rune_runner: Any | None = None,
        timeout_overrides: dict[str, int] | None = None,
        rune_api: Any | None = None,
    ) -> None:
        self._info = server_info
        self._rune_runner = rune_runner
        self._rune_api = rune_api
        self._timeout_overrides = timeout_overrides or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._http_base_url: str | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._tool_spells: list[SpellDefinition] = []
        self._closing = False  # guard against concurrent close() calls
        self._close_task: asyncio.Task[None] | None = None

    @property
    def tool_spells(self) -> list[SpellDefinition]:
        return list(self._tool_spells)

    async def connect(self) -> None:
        _check_sandbox(self._info)
        transport = self._info.transport
        if transport == "stdio":
            await self._connect_stdio()
        elif transport == "streamable-http":
            await self._connect_streamable_http()
        else:
            raise MCPTransportError(f"Unknown transport: {transport}")

    async def _connect_stdio(self) -> None:
        cmd = [self._info.config["command"]]
        cmd.extend(self._info.config.get("args", []))
        env = dict(self._info.config.get("env", {}))
        # Merge with parent environment; config env overrides parent
        merged = dict(os.environ)
        merged.update(env)
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # drain stderr to prevent deadlock
            env=merged,
        )
        await self._initialize()

    async def _connect_streamable_http(self) -> None:
        self._http_base_url = self._info.config.get("url", "").rstrip("/")
        if not self._http_base_url:
            raise MCPTransportError(
                "Streamable HTTP transport requires 'url' in config"
            )
        # Shared session reused across all requests with timeout (from config if present)
        http_timeout = self._timeout_overrides.get("http", _MCP_HTTP_TIMEOUT)
        timeout = aiohttp.ClientTimeout(total=http_timeout)
        self._http_session = aiohttp.ClientSession(timeout=timeout)
        try:
            await self._initialize()
        except Exception:
            await self._http_session.close()
            self._http_session = None
            raise

    async def _initialize(self) -> None:
        """Send initialize request with timeout.
        Validates protocolVersion in response, sends capabilities,
        and emits notifications/initialized per MCP spec.
        Uses config override from MCPSearchSpell timeout dict if present.
        """
        init_timeout = self._timeout_overrides.get("init", _MCP_INIT_TIMEOUT)
        try:
            result = await asyncio.wait_for(
                self._send_jsonrpc_raw(
                    method="initialize",
                    params={
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                            "prompts": {},
                        },
                        "clientInfo": {"name": "mvgeos", "version": "1.0.0"},
                    },
                    request_id="init-1",
                ),
                timeout=init_timeout,
            )
        except TimeoutError:
            raise MCPTransportError(f"MCP initialize timed out after {init_timeout}s")
        if "error" in result:
            raise MCPTransportError(
                f"MCP initialize failed: {result['error'].get('message', 'unknown')}"
            )
        server_version = result.get("protocolVersion", "")
        if server_version != MCP_PROTOCOL_VERSION:
            raise MCPTransportError(
                f"MCP protocol version mismatch: expected {MCP_PROTOCOL_VERSION}, "
                f"got {server_version}"
            )
        # Send notifications/initialized as required by MCP spec (no id per JSON-RPC 2.0)
        try:
            await self._send_jsonrpc_raw(
                method="notifications/initialized",
                params={},
                request_id="",
                notification=True,
            )
        except Exception:  # noqa: BLE001, S110 -- MCP server may disconnect after initialized notification; acceptable per spec
            # Server may disconnect after initialized notification;
            # this is acceptable per spec.
            pass

    async def send_jsonrpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: str | int = 1,
    ) -> dict[str, Any]:
        return await self._send_jsonrpc_raw(method, params or {}, request_id)

    async def _send_jsonrpc_raw(
        self,
        method: str,
        params: dict[str, Any],
        request_id: str | int,
        notification: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        # Per JSON-RPC 2.0, notifications MUST NOT have an id member
        if not notification:
            body["id"] = request_id
        request = json.dumps(body) + "\n"

        if self._proc is not None:
            return await self._send_stdio(request)
        elif self._http_session is not None:
            return await self._send_http(request)
        else:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "MCP not connected"}],
            }

    async def _send_stdio(self, request: str) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "stdio connection lost"}],
            }
        stdio_timeout = self._timeout_overrides.get("stdio", _MCP_STDIO_TIMEOUT)
        try:
            self._proc.stdin.write(request.encode())
            await asyncio.wait_for(self._proc.stdin.drain(), timeout=stdio_timeout)
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=stdio_timeout
            )
        except TimeoutError:
            raise MCPTransportError(
                f"stdio {self._info.name} timed out after {stdio_timeout}s"
            )
        if not line:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "empty response from MCP server"}],
            }
        try:
            response = json.loads(line.decode())
        except json.JSONDecodeError as e:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"invalid JSON from MCP server: {e}",
                    }
                ],
            }
        return response.get("result", response)

    async def _send_http(self, request: str) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._info.config.get("headers"):
            headers.update(self._info.config["headers"])

        http_timeout = self._timeout_overrides.get("http", _MCP_HTTP_TIMEOUT)
        try:
            async with self._http_session.post(
                self._http_base_url,
                data=request.encode(),
                headers=headers,
            ) as resp:
                # Streamable HTTP 202 Accepted means server will respond later
                # via SSE GET stream (not yet implemented)
                if resp.status == 202:
                    return {
                        "isError": False,
                        "content": [
                            {"type": "text", "text": "request accepted (SSE pending)"}
                        ],
                    }
                if not 200 <= resp.status < 300:
                    raise MCPTransportError(
                        f"HTTP {self._info.name} returned {resp.status} {resp.reason}"
                    )
                body = await resp.read()
        except (TimeoutError, aiohttp.ClientError) as exc:
            timeout_label = getattr(exc, "timeout", http_timeout)
            raise MCPTransportError(
                f"HTTP {self._info.name} timed out after {timeout_label}s"
            )
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"invalid JSON from MCP server: {e}",
                    }
                ],
            }
        if "error" in parsed:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": parsed["error"].get("message", "MCP server error"),
                    }
                ],
            }
        return parsed.get("result", parsed)

    # Maps JSON-RPC method names to their response field names.
    # e.g., "tools/list" returns {"tools": [...]}, not {"list": [...]}.
    _LIST_RESPONSE_KEY: ClassVar[dict[str, str]] = {
        "tools/list": "tools",
        "resources/list": "resources",
        "prompts/list": "prompts",
    }

    async def _list_with_pagination(self, method: str) -> list[dict[str, Any]]:
        """Paginate through list methods (tools/list, resources/list, prompts/list).
        Respects nextCursor for servers returning partial results.
        Enforces a maximum item limit to prevent unbounded registration.
        """
        max_items = {
            "tools/list": _MCP_MAX_TOOLS,
            "resources/list": _MCP_MAX_RESOURCES,
            "prompts/list": _MCP_MAX_PROMPTS,
        }.get(method, 100)

        response_key = self._LIST_RESPONSE_KEY.get(method, method.split("/")[1])

        items: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor

            tool_list_timeout = self._timeout_overrides.get(
                "tool_list", _MCP_TOOL_LIST_TIMEOUT
            )
            try:
                result = await asyncio.wait_for(
                    self.send_jsonrpc(method=method, params=params),
                    timeout=tool_list_timeout,
                )
            except TimeoutError:
                raise MCPTransportError(
                    f"{method} timed out after {tool_list_timeout}s"
                )

            batch = result.get(response_key, [])
            items.extend(batch)

            if len(items) >= max_items:
                items = items[:max_items]
                break

            cursor = result.get("nextCursor")
            if not cursor:
                break

        return items

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self._list_with_pagination("tools/list")

    async def list_resources(self) -> list[dict[str, Any]]:
        return await self._list_with_pagination("resources/list")

    async def list_prompts(self) -> list[dict[str, Any]]:
        return await self._list_with_pagination("prompts/list")

    async def register_all_capabilities(self) -> list[SpellDefinition]:
        # Fetch all capability lists in parallel
        tool_task = asyncio.ensure_future(self.list_tools())
        res_task = asyncio.ensure_future(self.list_resources())
        prompt_task = asyncio.ensure_future(self.list_prompts())
        tool_list, resources, prompts = await asyncio.gather(
            tool_task, res_task, prompt_task
        )

        seen_tools: set[str] = set()
        seen_resources: set[str] = set()
        seen_prompts: set[str] = set()
        spells_to_register: list[SpellDefinition] = []

        for tool_info in tool_list:
            name = tool_info["name"]
            if name in seen_tools:
                continue
            seen_tools.add(name)
            spell = MCPToolSpell(
                tool_name=name,
                description=tool_info.get("description", ""),
                input_schema=tool_info.get("inputSchema", {}),
                connector=self,
            )
            spells_to_register.append(spell)

        for res in resources:
            uri = res["uri"]
            if uri in seen_resources:
                continue
            seen_resources.add(uri)
            spell = MCPResourceSpell(
                uri=uri,
                name=res.get("name", uri.split("/")[-1]),
                description=res.get("description", ""),
                mime_type=res.get("mimeType", "text/plain"),
                connector=self,
            )
            spells_to_register.append(spell)

        for prompt in prompts:
            pname = prompt["name"]
            if pname in seen_prompts:
                continue
            seen_prompts.add(pname)
            spell = MCPPromptSpell(
                prompt_name=pname,
                description=prompt.get("description", ""),
                arguments=prompt.get("arguments", []),
                connector=self,
            )
            spells_to_register.append(spell)

        # Register all spells attributed to the seeker rune. Prefer the
        # seeker-scoped RuneAPI (auto-attributes source_rune="seeker" and
        # lands in seeker's own active entry pre-pin); fall back to the raw
        # runner with an explicit rune_name so tools never land in the
        # anonymous None bucket.
        if self._rune_api is not None:
            registered: list[SpellDefinition] = []
            for spell in spells_to_register:
                self._rune_api.register_spell(spell)
                registered.append(spell)
            self._tool_spells.extend(registered)
        elif self._rune_runner is not None:
            registered = []
            for spell in spells_to_register:
                self._rune_runner.register_spell(spell, rune_name="seeker")
                registered.append(spell)
            self._tool_spells.extend(registered)
        else:
            self._tool_spells.extend(spells_to_register)

        return list(self._tool_spells)

    async def close(self) -> None:
        """Shutdown MCP connection. Thread-safe: guarded by _closing flag."""
        if self._closing:
            # If already closing, wait for existing close task to complete
            if self._close_task is not None:
                await self._close_task
            return
        self._closing = True
        self._close_task = asyncio.ensure_future(self._close_impl())
        await self._close_task

    async def _close_impl(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                self._proc.kill()
                await self._proc.wait()
            finally:
                self._proc = None
        if self._http_session is not None:
            try:
                await asyncio.wait_for(self._http_session.close(), timeout=5)
            except TimeoutError:
                pass
            finally:
                self._http_session = None
