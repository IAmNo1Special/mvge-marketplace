"""Sub-Mvge summoning, persistent sub-sessions, and reactive wakeup bridge rune."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from mvge.agent.sub_mvge import (
        SubMvgeManager,
        SubMvgeRecord,
        SubMvgeSpec,
    )
from mvge.core.spells import SpellContext
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SpellDefinition

logger = logging.getLogger(__name__)

RESEARCH_PRESET_SPELLS: frozenset[str] = frozenset(
    {
        "read",
        "grep",
        "find",
        "list_files",
        "read_url",
        "search_web",
        "search",
        "activate",
        "unload",
    }
)


class SubMvgeBridgeState:
    def __init__(
        self,
        api: RuneAPI,
        manager_factory: Callable[..., SubMvgeManager] | None = None,
    ) -> None:
        self.api = api
        self._manager_factory = manager_factory
        self._manager: SubMvgeManager | None = None

    def get_manager(self, ctx: SpellContext | None = None) -> SubMvgeManager:
        if self._manager is None:
            if self._manager_factory is not None:
                self._manager = self._manager_factory()
            else:
                from mvge.agent.sub_mvge import SubMvgeManager

                runner = getattr(self.api, "_runner", None)
                runner_ctx = getattr(runner, "context", None)
                workspace = getattr(ctx, "workspace_root", None) or Path(
                    getattr(runner_ctx, "cwd", Path.cwd())
                )
                agent_name = getattr(ctx, "agent_name", None) or getattr(
                    runner_ctx, "agent_name", "coding_mvge"
                )
                registry = getattr(runner, "_provider_registry", None)

                self._manager = SubMvgeManager(
                    parent_workspace=workspace,
                    parent_agent_name=agent_name,
                    parent_model_id=getattr(runner_ctx, "model_id", "") or None,
                    shared_runner=runner,
                    shared_realm_registry=registry,
                )
        return self._manager

    async def shutdown(self) -> None:
        if self._manager is not None:
            await self._manager.kill_all()
            self._manager = None


def _format_record(rec: SubMvgeRecord) -> dict[str, Any]:
    return {
        "sub_mvge_id": rec.sub_mvge_id,
        "role": rec.role,
        "agent_name": rec.agent_name,
        "model_id": rec.model_id,
        "child_tome_id": rec.child_tome_id,
        "tome_path": str(rec.tome_path),
        "status": rec.state.value,
        "state_detail": rec.state_detail,
        "summary": rec.summary,
        "error": rec.error,
        "mana_used": rec.mana_used,
    }


def rune_factory(
    api: RuneAPI,
    manager_factory: Callable[..., SubMvgeManager] | None = None,
) -> None:
    """Initialize and register sub-Mvge summoning spells."""
    state = SubMvgeBridgeState(api, manager_factory=manager_factory)
    api.on_dispose(state.shutdown)

    async def handle_summon(
        params: dict[str, Any] | None = None,
        *args: Any,
        subagents: list[dict[str, Any]] | None = None,
        wait: bool = True,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Spawn and coordinate one or more child sub-Mvges."""
        manager = state.get_manager(ctx)
        p = dict(params or {})
        if kwargs:
            p.update(kwargs)

        sub_list = p.get("subagents") or subagents or []
        wait_flag = p.get("wait", wait)
        specs: list[SubMvgeSpec] = []

        for sub in sub_list:
            role = str(sub.get("role", "worker"))
            prompt = str(sub.get("prompt", ""))
            agent_name = sub.get("type_name") or sub.get("agent_name")
            model = str(sub.get("model", "inherit"))
            workspace: Literal["inherit", "worktree"] = (
                "worktree" if sub.get("workspace") == "worktree" else "inherit"
            )

            allowed = sub.get("allowed_spells")
            if allowed is not None:
                allowed_spells: frozenset[str] | None = frozenset(allowed)
            elif role in ("research", "codebase_researcher"):
                allowed_spells = RESEARCH_PRESET_SPELLS
            elif role == "self":
                allowed_spells = None
            else:
                allowed_spells = None

            from mvge.agent.sub_mvge import SubMvgeSpec

            specs.append(
                SubMvgeSpec(
                    role=role,
                    prompt=prompt,
                    agent_name=agent_name,
                    model=model,
                    workspace=workspace,
                    allowed_spells=allowed_spells,
                    wait=wait_flag,
                )
            )

        parent_signal = getattr(ctx, "signal", None)
        parent_cast_id = getattr(ctx, "spell_cast_id", None)

        records = await manager.summon(
            specs,
            parent_signal=parent_signal,
            parent_spell_cast_id=parent_cast_id,
        )

        return {
            "success": True,
            "subagents": [_format_record(r) for r in records],
        }

    async def handle_message(
        params: dict[str, Any] | None = None,
        *args: Any,
        sub_mvge_id: str | None = None,
        message: str | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a follow-up or steering message to an existing sub-Mvge."""
        manager = state.get_manager(ctx)
        p = dict(params or {})
        if kwargs:
            p.update(kwargs)

        target_id = str(p.get("sub_mvge_id") or sub_mvge_id or "")
        msg = str(p.get("message") or message or "")

        rec = await manager.send_message(target_id, msg)
        return {
            "success": True,
            "sub_mvge_id": rec.sub_mvge_id,
            "status": rec.state.value,
            "summary": rec.summary,
            "error": rec.error,
        }

    async def handle_manage(
        params: dict[str, Any] | None = None,
        *args: Any,
        action: Literal["list", "kill", "kill_all"] | str | None = None,
        sub_mvge_ids: list[str] | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List or terminate tracked sub-Mvges."""
        manager = state.get_manager(ctx)
        p = dict(params or {})
        if kwargs:
            p.update(kwargs)

        act = str(p.get("action") or action or "list")
        ids = p.get("sub_mvge_ids") or sub_mvge_ids or []

        if act == "list":
            records = manager.list_active()
        elif act == "kill":
            records = await manager.kill(ids)
        elif act == "kill_all":
            records = await manager.kill_all()
        else:
            raise ValueError(f"Unknown manage_mvges action: '{act}'")

        return {
            "success": True,
            "action": act,
            "subagents": [_format_record(r) for r in records],
        }

    api.register_spell(
        SpellDefinition(
            name="summon_mvge",
            description=(
                "Spawn one or more sub-Mvges concurrently to perform focused "
                "tasks with persistent subsessions, scoped spells, and reactive wakeup."
            ),
            # A wait:true summon runs a whole child agent turn, which takes
            # far longer than the dispatcher's 30s spell wall clock on any
            # lane, let alone a slow keyless one. Opt out of that timeout:
            # the child turn is bounded by its own max_turns and provider
            # timeouts, and the parent turn's abort still cancels the cast.
            timeout_ms=0,
            parameters={
                "type": "object",
                "properties": {
                    "subagents": {
                        "type": "array",
                        "description": "List of sub-Mvge specifications to launch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Role description (e.g. 'research', 'self', 'worker').",
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Task instruction for the sub-Mvge.",
                                },
                                "type_name": {
                                    "type": "string",
                                    "description": "Agent name to summon (default coding_mvge).",
                                },
                                "model": {
                                    "type": "string",
                                    "description": "Model ID to use, or 'inherit'.",
                                    "default": "inherit",
                                },
                                "workspace": {
                                    "type": "string",
                                    "enum": ["inherit", "worktree"],
                                    "description": "Workspace isolation mode.",
                                    "default": "inherit",
                                },
                                "allowed_spells": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Restricted subset of spells.",
                                },
                            },
                            "required": ["role", "prompt"],
                        },
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "If True, awaits completion synchronously. If False, runs in background and wakes parent reactively.",
                        "default": True,
                    },
                },
                "required": ["subagents"],
            },
            handler=handle_summon,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="message_mvge",
            description=(
                "Send steering or follow-up instructions to an active sub-Mvge on its existing Tome context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sub_mvge_id": {
                        "type": "string",
                        "description": "The unique identifier of the sub-Mvge.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Instruction message to send.",
                    },
                },
                "required": ["sub_mvge_id", "message"],
            },
            handler=handle_message,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="manage_mvges",
            description="Inspect or terminate active sub-Mvges.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "kill", "kill_all"],
                        "description": "Management action to perform.",
                    },
                    "sub_mvge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs to terminate when action is 'kill'.",
                    },
                },
                "required": ["action"],
            },
            handler=handle_manage,
        )
    )


__all__ = ["rune_factory"]
