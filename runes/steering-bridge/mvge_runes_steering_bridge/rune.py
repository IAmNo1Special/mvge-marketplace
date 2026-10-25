from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mvge_runes_steering_bridge.prompt import (
    build_steering_section,
    discover_subpackage_pointers,
)
from mvge_runes_steering_bridge.resolver import (
    read_steering_text,
    resolve_global_agents_file,
    resolve_workspace_agents_file,
)
from mvge_runes_steering_bridge.types import SteeringState

logger = logging.getLogger(__name__)


def default_global_dir() -> Path | None:
    """Resolve the global steering directory without touching the host.

    Returns the ``MVGE_GLOBAL_DIR`` override when set (the hermetic
    seam used by tests and embedders), otherwise None so the resolver
    falls back to the .agents Protocol default (``~/.agents``).
    """
    override = os.environ.get("MVGE_GLOBAL_DIR")
    return Path(override).expanduser() if override else None


class SteeringBridgeRune:
    """Rune binding repository steering (AGENTS.md) to Mvge prompts."""

    def __init__(self, api: Any | None = None) -> None:
        self._api = api
        self.state = SteeringState()

    def refresh_steering(
        self,
        cwd: Path | str | None = None,
        global_dir: Path | str | None = None,
    ) -> SteeringState:
        """Resolve workspace, global, and subpackage steering for ``cwd``."""
        base = Path(cwd) if cwd is not None else Path.cwd()
        if global_dir is not None:
            g_dir: Path | None = Path(global_dir)
        else:
            g_dir = default_global_dir()

        ws_ref = resolve_workspace_agents_file(base)
        gl_ref = resolve_global_agents_file(g_dir)
        subs = discover_subpackage_pointers(base)
        section = build_steering_section(ws_ref, gl_ref, subs)

        self.state = SteeringState(
            cwd=base,
            global_dir=g_dir,
            workspace_ref=ws_ref,
            global_ref=gl_ref,
            subpackages=subs,
            section=section,
        )
        return self.state

    async def on_session_start(self, data: Any = None) -> None:
        """Resolve steering paths on session startup."""
        cwd_path: Path | str | None = None
        if self._api and hasattr(self._api, "context"):
            ctx = self._api.context
            raw_cwd = getattr(ctx, "cwd", None)
            cwd_path = Path(raw_cwd) if raw_cwd else None

        self.refresh_steering(cwd=cwd_path)

    async def on_before_mvge_start(self, data: Any = None) -> Any:
        """Inject the steering section into the dynamic prompt."""
        if not self.state.section:
            # Lazy refresh so steering still applies when session_start
            # was skipped (e.g. embedded use). Cwd is read from the event
            # payload when available, else the last explicitly refreshed
            # cwd — never the bare process cwd, which may be unrelated to
            # the session's workspace.
            event_cwd: Path | str | None = None
            if hasattr(data, "cwd"):
                event_cwd = getattr(data, "cwd", None)
            elif isinstance(data, dict) and data.get("cwd"):
                event_cwd = data["cwd"]
            if event_cwd is None:
                event_cwd = self.state.cwd
            self.refresh_steering(cwd=event_cwd, global_dir=self.state.global_dir)

        if not self.state.section:
            return data

        section = self.state.section
        if hasattr(data, "base_prompt"):
            prompt = getattr(data, "base_prompt", "")
            data.base_prompt = f"{prompt}\n\n{section}" if prompt else section
            return data
        elif isinstance(data, dict):
            if "base_prompt" in data:
                prompt = data.get("base_prompt", "")
                data["base_prompt"] = f"{prompt}\n\n{section}" if prompt else section
            else:
                prompt = data.get("prompt", "")
                data["prompt"] = f"{prompt}\n\n{section}" if prompt else section
            return data
        elif isinstance(data, str):
            return f"{data}\n\n{section}" if data else section
        return data

    async def on_session_shutdown(self, data: Any = None) -> None:
        """Clear cached steering state on session shutdown."""
        self.state = SteeringState()

    def status_lines(self) -> list[str]:
        """Render ASCII status lines for the resolved steering layers."""
        if (
            self.state.workspace_ref is None
            and self.state.global_ref is None
            and not self.state.subpackages
        ):
            return ["MISSING: No AGENTS.md steering files resolved."]

        lines = ["OK: Repository steering resolved:"]
        if self.state.global_ref is not None:
            lines.append(f"  - Global Rules: {self.state.global_ref[1]}")
        if self.state.workspace_ref is not None:
            lines.append(f"  - Project Rules: {self.state.workspace_ref[1]}")
        for dir_name, rel_path in self.state.subpackages:
            lines.append(f"  - Subpackage Rules ({dir_name}): {rel_path}")
        return lines

    def validate(self) -> list[str]:
        """Validate resolved steering files, returning ASCII report lines."""
        problems: list[str] = []
        for label, ref in (
            ("Global", self.state.global_ref),
            ("Project", self.state.workspace_ref),
        ):
            if ref is None:
                continue
            content = read_steering_text(ref[0], strip_frontmatter=True)
            if not content:
                problems.append(f"FAIL: {label} steering file is empty: {ref[1]}")

        if problems:
            return problems
        if self.state.workspace_ref is None and self.state.global_ref is None:
            return ["MISSING: No AGENTS.md steering files to validate."]
        count = sum(
            1
            for ref in (self.state.workspace_ref, self.state.global_ref)
            if ref is not None
        )
        return [f"OK: {count} steering file(s) valid."]

    async def handle_slash_command(self, args_str: str = "") -> str:
        """Handle /steering [status|show|validate]."""
        parts = args_str.strip().split()
        subcmd = parts[0] if parts else "status"

        if subcmd == "show":
            if not self.state.section:
                return "MISSING: No steering section resolved."
            return self.state.section
        if subcmd == "validate":
            return "\n".join(self.validate())
        if subcmd in ("status", ""):
            return "\n".join(self.status_lines())
        return (
            f"Unknown steering command: '{subcmd}'. "
            "Usage: /steering [status|show|validate]"
        )


def rune_factory(api: Any) -> SteeringBridgeRune:
    """Instantiate and register the steering-bridge rune."""
    from mvge.runes.types import SigilHook

    rune = SteeringBridgeRune(api)

    if hasattr(api, "on"):
        api.on(SigilHook.SESSION_START, rune.on_session_start)
        api.on(SigilHook.BEFORE_MVGE_START, rune.on_before_mvge_start)
        api.on(SigilHook.SESSION_SHUTDOWN, rune.on_session_shutdown)
    elif hasattr(api, "register_sigil"):
        api.register_sigil(SigilHook.SESSION_START, rune.on_session_start)
        api.register_sigil(SigilHook.BEFORE_MVGE_START, rune.on_before_mvge_start)
        api.register_sigil(SigilHook.SESSION_SHUTDOWN, rune.on_session_shutdown)

    if hasattr(api, "register_command"):
        api.register_command(
            "steering",
            "Show repository steering (AGENTS.md) status",
            rune.handle_slash_command,
        )

    return rune
