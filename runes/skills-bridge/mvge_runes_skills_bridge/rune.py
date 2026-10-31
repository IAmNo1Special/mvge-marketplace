from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mvge_runes_skills_bridge.activation import activate_skill
from mvge_runes_skills_bridge.loader import (
    clear_skill_manifest_cache,
    discover_plugin_skill_paths,
    get_prioritized_skill_search_paths,
    load_skills_from_paths,
)
from mvge_runes_skills_bridge.prompt import (
    build_skill_catalog,
    update_invocations_with_skill_catalog,
)
from mvge_runes_skills_bridge.types import SkillManifest

logger = logging.getLogger(__name__)


class SkillsBridgeRune:
    """Rune binding Agent Skills (agentskills.io) and Agent Plugins to Mvge."""

    def __init__(self, api: Any | None = None) -> None:
        self._api = api
        self.skills_map: dict[str, SkillManifest] = {}
        self.active_skills: set[str] = set()
        self.suppress_catalog: bool = False
        self.diagnostics: list[Any] = []

    def refresh_skills(
        self,
        agent_name: str = "",
        cwd: Path | None = None,
        global_dir: Path | None = None,
    ) -> None:
        """Discover skills across project, user, agent, and plugin scopes."""
        paths = get_prioritized_skill_search_paths(
            agent_name, cwd=cwd, global_dir=global_dir
        )
        plugin_paths = discover_plugin_skill_paths(cwd=cwd, global_dir=global_dir)
        all_paths = paths + plugin_paths

        loads, diags = load_skills_from_paths(all_paths, agent_name)
        self.diagnostics = diags
        self.skills_map = {load.manifest.name: load.manifest for load in loads}
        self.active_skills.clear()
        if self._api is not None:
            runner = getattr(self._api, "_runner", None)
            if runner is not None and hasattr(runner, "skill_catalog"):
                catalog = runner.skill_catalog
                # Drop the previous skills-bridge loads first so a
                # refresh does not duplicate them in the engine catalog.
                # Guarded: unpatched engines have no clear_rune method.
                if hasattr(catalog, "clear_rune"):
                    catalog.clear_rune("skills-bridge")
                for load in loads:
                    if getattr(load, "source_rune", None) is None:
                        load.source_rune = "skills-bridge"
                catalog.load_skills(loads, diagnostics=diags)

    async def on_session_start(self, data: Any = None) -> None:
        """Discover skills on session startup."""
        cwd_path = Path.cwd()
        agent_name = ""
        if self._api and hasattr(self._api, "context"):
            ctx = self._api.context
            cwd_path = Path(ctx.cwd) if getattr(ctx, "cwd", None) else Path.cwd()
            agent_name = getattr(ctx, "agent_name", "")

        self.refresh_skills(agent_name=agent_name, cwd=cwd_path)

    async def rehydrate(self, payload: Any = None) -> None:
        """Refresh skills on reload, without refiring SESSION_START.

        The manifest cache is cleared first so an edit that preserved
        the file's mtime is still re-read. Each field resolves per
        field from the payload first (attribute access, dict fallback),
        then from the API context, then from defaults; an empty-string
        cwd counts as missing and falls through. The engine payload's
        cwd is a string, so it is converted with Path(...) exactly as
        on_session_start does, since refresh_skills takes a Path.
        """
        clear_skill_manifest_cache()

        def _payload_field(name: str) -> Any:
            if isinstance(payload, dict):
                return payload.get(name)
            return getattr(payload, name, None)

        context_cwd: Path | None = None
        context_agent_name = ""
        if self._api is not None and hasattr(self._api, "context"):
            ctx = self._api.context
            raw_context_cwd = getattr(ctx, "cwd", None)
            if isinstance(raw_context_cwd, Path):
                context_cwd = raw_context_cwd
            elif isinstance(raw_context_cwd, str) and raw_context_cwd:
                context_cwd = Path(raw_context_cwd)
            raw_context_agent = getattr(ctx, "agent_name", None)
            if isinstance(raw_context_agent, str):
                context_agent_name = raw_context_agent

        raw_payload_cwd = _payload_field("cwd")
        if isinstance(raw_payload_cwd, Path):
            cwd_path = raw_payload_cwd
        elif isinstance(raw_payload_cwd, str) and raw_payload_cwd:
            cwd_path = Path(raw_payload_cwd)
        elif context_cwd is not None:
            cwd_path = context_cwd
        else:
            cwd_path = Path.cwd()

        raw_payload_agent = _payload_field("agent_name")
        if isinstance(raw_payload_agent, str) and raw_payload_agent:
            agent_name = raw_payload_agent
        elif context_agent_name:
            agent_name = context_agent_name
        else:
            agent_name = ""

        self.refresh_skills(agent_name=agent_name, cwd=cwd_path)

    async def on_before_mvge_start(self, data: Any = None) -> Any:
        """Inject available skills catalog into dynamic prompt."""
        if (
            self.suppress_catalog
            or getattr(self._api, "has_spell_gateway", False) is True
            or not self.skills_map
        ):
            return data

        catalog = build_skill_catalog(
            list(self.skills_map.values()), suppress=self.suppress_catalog
        )
        if not catalog:
            return data

        if hasattr(data, "base_prompt"):
            prompt = getattr(data, "base_prompt", "")
            data.base_prompt = f"{prompt}\n\n{catalog}" if prompt else catalog
            return data
        elif isinstance(data, dict):
            prompt = data.get("prompt", "") or data.get("base_prompt", "")
            if "base_prompt" in data:
                data["base_prompt"] = f"{prompt}\n\n{catalog}" if prompt else catalog
            else:
                data["prompt"] = f"{prompt}\n\n{catalog}" if prompt else catalog
            return data
        elif isinstance(data, str):
            return f"{data}\n\n{catalog}" if data else catalog
        return data

    async def on_context_transform(self, invocations: list[Any]) -> list[Any]:
        """In-place update of skill catalog across multi-turn sessions."""
        suppress = (
            self.suppress_catalog
            or getattr(self._api, "has_spell_gateway", False) is True
        )
        return update_invocations_with_skill_catalog(
            invocations,
            list(self.skills_map.values()),
            suppress=suppress,
        )

    async def on_session_shutdown(self, data: Any = None) -> None:
        """Clean up memory cache on session shutdown."""
        clear_skill_manifest_cache()
        self.active_skills.clear()

    async def handle_activate_skill(
        self,
        spell_cast_id: str | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute the activate_skill spell."""
        parameters = params or {}
        if spell_cast_id is not None and isinstance(spell_cast_id, dict):
            parameters = spell_cast_id

        name = str(parameters.get("name", "")).strip()
        if not name:
            return {"error": "Missing required parameter 'name'"}

        try:
            result = activate_skill(name, self.skills_map, self.active_skills)
            return {
                "name": result.name,
                "content": result.content,
                "location": result.location,
                "resources": result.resources,
            }
        except ValueError as exc:
            return {"error": str(exc)}

    async def handle_slash_command(self, args_str: str = "") -> str:
        """Handle /skill <name> or /skills command."""
        skill_name = args_str.strip()
        if not skill_name:
            if not self.skills_map:
                return "MISSING: No skills currently installed."
            return "Available skills: " + ", ".join(sorted(self.skills_map.keys()))

        res = await self.handle_activate_skill(params={"name": skill_name})
        if "error" in res:
            return f"FAIL: {res['error']}"
        return f"OK: Skill '{skill_name}' activated.\n\n{res['content']}"


def rune_factory(api: Any) -> SkillsBridgeRune:
    """Instantiate and register the skills-bridge rune."""
    from mvge.runes.types import ExecutionMode, SigilHook, SpellDefinition

    rune = SkillsBridgeRune(api)

    if hasattr(api, "on"):
        api.on(SigilHook.SESSION_START, rune.on_session_start)
        api.on(SigilHook.BEFORE_MVGE_START, rune.on_before_mvge_start)
        api.on(SigilHook.CONTEXT_TRANSFORM, rune.on_context_transform)
        api.on(SigilHook.SESSION_SHUTDOWN, rune.on_session_shutdown)
    elif hasattr(api, "register_sigil"):
        api.register_sigil(SigilHook.SESSION_START, rune.on_session_start)
        api.register_sigil(SigilHook.BEFORE_MVGE_START, rune.on_before_mvge_start)
        api.register_sigil(SigilHook.CONTEXT_TRANSFORM, rune.on_context_transform)
        api.register_sigil(SigilHook.SESSION_SHUTDOWN, rune.on_session_shutdown)

    if hasattr(api, "register_spell"):
        from mvge.runes.types import ExecutionMode, SpellDefinition

        spell = SpellDefinition(
            name="activate_skill",
            description=(
                "Activate a registered skill by name to load its specialized "
                "instructions and inspect its bundled resources."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The exact name of the skill to activate",
                    }
                },
                "required": ["name"],
            },
            execution_mode=ExecutionMode.PARALLEL,
            source_rune="skills-bridge",
            handler=rune.handle_activate_skill,
        )
        api.register_spell(spell)

    if hasattr(api, "register_command"):
        api.register_command(
            "skill", "Manage and activate agent skills", rune.handle_slash_command
        )
        api.register_command(
            "skills", "List available agent skills", rune.handle_slash_command
        )

    return rune
