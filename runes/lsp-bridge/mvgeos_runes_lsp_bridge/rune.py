from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from mvge.core.events import ContentType
from mvge.core.spells import SpellResultMessage
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

_SERVER_SPECS: dict[str, tuple[str, list[list[str]]]] = {
    # extension -> (language_id, list of candidate command lines)
    ".py": (
        "python",
        [["basedpyright-langserver", "--stdio"], ["pyright-langserver", "--stdio"]],
    ),
    ".pyi": (
        "python",
        [["basedpyright-langserver", "--stdio"], ["pyright-langserver", "--stdio"]],
    ),
    ".ts": ("typescript", [["typescript-language-server", "--stdio"]]),
    ".tsx": ("typescript", [["typescript-language-server", "--stdio"]]),
    ".js": ("javascript", [["typescript-language-server", "--stdio"]]),
    ".jsx": ("javascript", [["typescript-language-server", "--stdio"]]),
    ".mjs": ("javascript", [["typescript-language-server", "--stdio"]]),
    ".cjs": ("javascript", [["typescript-language-server", "--stdio"]]),
    ".rs": ("rust", [["rust-analyzer"]]),
    ".go": ("go", [["gopls"]]),
}


class LspClient:
    """Async stdio JSON-RPC 2.0 client for a single language server process."""

    def __init__(
        self, command: list[str], workspace_root: Path, language_id: str
    ) -> None:
        self.command = command
        self.workspace_root = workspace_root
        self.language_id = language_id
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._latest_diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diagnostic_events: dict[str, asyncio.Event] = {}
        self._doc_versions: dict[str, int] = {}
        self._closed = False

    async def start(self) -> bool:
        if self._process is not None:
            return True
        binary = self.command[0]
        if not shutil.which(binary):
            return False

        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.workspace_root),
            )
            self._reader_task = asyncio.create_task(self._read_loop())

            # Initialize handshake
            init_params = {
                "processId": os.getpid(),
                "rootUri": self.workspace_root.resolve().as_uri(),
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {
                            "relatedInformation": True,
                            "versionSupport": True,
                        }
                    }
                },
            }
            await self.send_request("initialize", init_params, timeout=5.0)
            await self.send_notification("initialized", {})
            return True
        except (OSError, RuntimeError, TimeoutError):
            await self.stop()
            return False

    async def send_request(self, method: str, params: Any, timeout: float = 5.0) -> Any:
        if self._process is None or self._process.stdin is None or self._closed:
            raise RuntimeError("LspClient process not running")
        req_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[req_id] = future

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        payload = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")

        self._process.stdin.write(header + payload)
        await self._process.stdin.drain()

        return await asyncio.wait_for(future, timeout=timeout)

    async def send_notification(self, method: str, params: Any) -> None:
        if self._process is None or self._process.stdin is None or self._closed:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        payload = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")

        self._process.stdin.write(header + payload)
        await self._process.stdin.drain()

    async def notify_document(self, path: Path, text: str) -> None:
        uri = path.resolve().as_uri()
        if uri not in self._doc_versions:
            self._doc_versions[uri] = 1
            await self.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self.language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
        else:
            self._doc_versions[uri] += 1
            await self.send_notification(
                "textDocument/didChange",
                {
                    "textDocument": {
                        "uri": uri,
                        "version": self._doc_versions[uri],
                    },
                    "contentChanges": [{"text": text}],
                },
            )

    async def wait_for_diagnostics(
        self,
        uri: str,
        debounce_ms: int = 150,
        timeout_ms: int = 3000,
    ) -> list[dict[str, Any]]:
        event = self._diagnostic_events.setdefault(uri, asyncio.Event())
        start_time = time.monotonic()
        timeout_sec = timeout_ms / 1000.0
        debounce_sec = debounce_ms / 1000.0

        while True:
            elapsed = time.monotonic() - start_time
            remaining = timeout_sec - elapsed
            if remaining <= 0:
                break

            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                break

            event.clear()
            # Debounce: wait 150ms for subsequent diagnostic messages to settle
            await asyncio.sleep(debounce_sec)
            if not event.is_set():
                # Debounce settled without further notifications
                break

        return self._latest_diagnostics.get(uri, [])

    async def _read_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return

        reader = self._process.stdout
        buffer = b""

        try:
            while not self._closed:
                # Read until header terminator \r\n\r\n
                header_pos = buffer.find(b"\r\n\r\n")
                while header_pos == -1 and not self._closed:
                    chunk = await reader.read(4096)
                    if not chunk:
                        return
                    buffer += chunk
                    header_pos = buffer.find(b"\r\n\r\n")

                if self._closed:
                    return

                header_bytes = buffer[:header_pos]
                buffer = buffer[header_pos + 4 :]

                # Parse Content-Length
                content_length = 0
                for line in header_bytes.decode("ascii", errors="replace").split(
                    "\r\n"
                ):
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())
                        break

                if content_length <= 0:
                    continue

                # Read content_length bytes
                while len(buffer) < content_length and not self._closed:
                    chunk = await reader.read(max(4096, content_length - len(buffer)))
                    if not chunk:
                        return
                    buffer += chunk

                body = buffer[:content_length]
                buffer = buffer[content_length:]

                try:
                    data = json.loads(body.decode("utf-8"))
                    self._dispatch_message(data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        except asyncio.CancelledError:
            pass
        except OSError:
            pass

    def _dispatch_message(self, data: dict[str, Any]) -> None:
        # Check if response to pending request
        msg_id = data.get("id")
        if isinstance(msg_id, int) and msg_id in self._pending_requests:
            fut = self._pending_requests.pop(msg_id)
            if not fut.done():
                if "error" in data:
                    fut.set_exception(RuntimeError(data["error"]))
                else:
                    fut.set_result(data.get("result"))
            return

        # Check if notification
        method = data.get("method")
        if method == "textDocument/publishDiagnostics":
            params = data.get("params", {})
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics", [])
            if isinstance(diagnostics, list):
                self._latest_diagnostics[uri] = diagnostics
                if uri in self._diagnostic_events:
                    self._diagnostic_events[uri].set()

    async def stop(self) -> None:
        self._closed = True
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

        if self._process is not None:
            try:
                if self._process.returncode is None:
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=1.0)
                    except TimeoutError:
                        self._process.kill()
            except OSError:
                pass
            self._process = None


class LspBridge:
    """Manages language server instances and hooks edit/write spell results."""

    def __init__(self, api: RuneAPI) -> None:
        self.api = api
        self._clients: dict[tuple[str, str], LspClient] = {}
        self._lock = asyncio.Lock()

    def get_server_command(self, ext: str) -> tuple[str, list[str]] | None:
        spec = _SERVER_SPECS.get(ext)
        if not spec:
            return None
        lang_id, candidate_commands = spec
        for cmd in candidate_commands:
            if shutil.which(cmd[0]):
                return lang_id, cmd
        return None

    async def get_or_create_client(
        self, workspace_root: Path, language_id: str, command: list[str]
    ) -> LspClient | None:
        key = (str(workspace_root.resolve()), language_id)
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = LspClient(command, workspace_root, language_id)
                ok = await client.start()
                if not ok:
                    return None
                self._clients[key] = client
            return client

    def format_diagnostics(
        self, diagnostics: list[dict[str, Any]], file_path: str, max_errors: int = 20
    ) -> str:
        # Filter for severity == 1 (Error)
        errors: list[str] = []
        for diag in diagnostics:
            if diag.get("severity") != 1:
                continue
            rng = diag.get("range", {}).get("start", {})
            line = int(rng.get("line", 0)) + 1
            col = int(rng.get("character", 0)) + 1
            msg = str(diag.get("message", "")).strip()
            errors.append(f"ERROR [{line}:{col}] {msg}")
            if len(errors) >= max_errors:
                break

        if not errors:
            return ""

        body = "\n".join(errors)
        return f'<diagnostics file="{file_path}">\n{body}\n</diagnostics>'

    def _extract_file_path(self, result: Any, data: Any) -> str | None:
        # Try details["path"]
        if isinstance(result, SpellResultMessage) and result.details:
            p = result.details.get("path")
            if isinstance(p, str) and p:
                return p
        if isinstance(result, dict):
            details = result.get("details")
            if isinstance(details, dict):
                p = details.get("path")
                if isinstance(p, str) and p:
                    return p

        # Try content string regex (e.g. "Updated foo.py", "Wrote to foo.py")
        text = ""
        if isinstance(result, SpellResultMessage):
            for block in result.content:
                if isinstance(block, dict) and block.get("type") in (
                    ContentType.TEXT,
                    "text",
                ):
                    text += block.get("text", "")
        elif isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text += block.get("text", "")
            elif isinstance(content, str):
                text = content

        m = re.search(r"(?:Updated|Wrote to)\s+([^\r\n]+)", text)
        if m:
            return m.group(1).strip()

        # Try data arguments
        if isinstance(data, dict):
            args = data.get("arguments") or data.get("spell_cast", {}).get("arguments")
            if isinstance(args, dict) and "path" in args:
                return str(args["path"])

        return None

    async def on_after_spell_result(self, data: Any) -> Any:
        spell_name = getattr(data, "spell_name", None)
        if spell_name is None and isinstance(data, dict):
            spell_name = data.get("spell_name")

        if spell_name not in ("edit", "write"):
            return data

        raw_result = getattr(data, "result", None)
        if raw_result is None and isinstance(data, dict):
            raw_result = data.get("result")

        file_path_str = self._extract_file_path(raw_result, data)
        if not file_path_str:
            return data

        file_path = Path(file_path_str)
        if not file_path.exists():
            return data

        ext = file_path.suffix.lower()
        spec = self.get_server_command(ext)
        if spec is None:
            return data

        lang_id, command = spec
        workspace_root = Path.cwd()

        client = await self.get_or_create_client(workspace_root, lang_id, command)
        if client is None:
            return data

        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            return data

        try:
            await client.notify_document(file_path, content)
            uri = file_path.resolve().as_uri()
            diagnostics = await client.wait_for_diagnostics(
                uri, debounce_ms=150, timeout_ms=3000
            )
            diag_text = self.format_diagnostics(diagnostics, file_path_str)
            if not diag_text:
                return data

            # Append diagnostics to spell result
            if isinstance(raw_result, SpellResultMessage):
                appended = False
                for block in raw_result.content:
                    if isinstance(block, dict) and block.get("type") in (
                        ContentType.TEXT,
                        "text",
                    ):
                        block["text"] = str(block.get("text", "")) + f"\n\n{diag_text}"
                        appended = True
                        break
                if not appended:
                    raw_result.content.append(
                        {"type": ContentType.TEXT, "text": diag_text}
                    )
            elif isinstance(raw_result, dict):
                content_blocks = raw_result.get("content")
                if isinstance(content_blocks, list):
                    appended = False
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") in (
                            ContentType.TEXT,
                            "text",
                        ):
                            block["text"] = (
                                str(block.get("text", "")) + f"\n\n{diag_text}"
                            )
                            appended = True
                            break
                    if not appended:
                        content_blocks.append({"type": "text", "text": diag_text})
                elif isinstance(content_blocks, str):
                    raw_result["content"] = content_blocks + f"\n\n{diag_text}"
        except (OSError, RuntimeError, TimeoutError):
            pass

        return data

    async def shutdown(self) -> None:
        async with self._lock:
            for client in self._clients.values():
                await client.stop()
            self._clients.clear()


def rune_factory(api: RuneAPI) -> LspBridge:
    bridge = LspBridge(api)

    async def handle_after_spell_result(data: Any) -> Any:
        return await bridge.on_after_spell_result(data)

    api.on(SigilHook.AFTER_SPELL_RESULT, handle_after_spell_result)

    async def cleanup() -> None:
        await bridge.shutdown()

    api.on_dispose(cleanup)
    return bridge
