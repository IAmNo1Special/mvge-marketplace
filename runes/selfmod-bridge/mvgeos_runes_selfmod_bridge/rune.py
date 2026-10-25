"""selfmod-bridge rune.

Exposes the assistant's own file-level self-modification tools as spells
(creation) and mind-ops (SYSTEM.md, spells dir, snapshots), so the agent
can do to itself what it does for users — scaffold spell, rune, or skill;
edit its persona; teach itself; snapshot and roll back.

Spec §5.3 hook contract: the hook handler reads the ``before_mvge_start``
payload attributes FIRST with ``getattr`` defaults, falls back to mapping
access for dict-shaped payloads, and passes anything else through
opaque — it must not crash on future payload shapes. New payload fields
absent on older engines degrade to safe defaults (no prompt section, no
state). The prompt section is appended EXACTLY once.

Rune separation of concerns / approval: this rune carries no private
execution gate (spec §5.6 — deliberately out of scope). ``read_only=False``
spells defer to the engine's approval rune when present.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mvge.runes import ExecutionMode
from mvge.runes.types import SigilHook, SpellDefinition

from mvgeos_runes_selfmod_bridge.prompt import build_selfmod_section
from mvgeos_runes_selfmod_bridge.spells import _SPELL_PARAMETERS, SelfmodSpellsMixin
from mvgeos_runes_selfmod_bridge.state import SelfmodState
from mvgeos_runes_selfmod_bridge.status import describe_extensions

logger = logging.getLogger(__name__)


class SelfmodBridgeRune(SelfmodSpellsMixin):
    """Self-modification bridge rune.

    On ``before_mvge_start`` the hook rebuilds this session's self-mod
    state (agent config dir, runes paths, system prompt file, spells dir)
    from the payload and appends the selfmod prompt section once. Spells
    use ``self.state``; when state is missing (never initialized) every
    handler fails loudly with ``state_not_initialized`` rather than
    guessing paths.
    """

    def __init__(self, api: Any, manifest: dict[str, Any]) -> None:
        self.api = api
        self.manifest = manifest
        self.name = "selfmod-bridge"
        self.version = manifest.get("version", "0.1.0")
        self.state: SelfmodState | None = None
        self._registered = False

    # -- hook ----------------------------------------------------------

    @staticmethod
    def _payload_get(payload: Any, field: str, default: Any = None) -> Any:
        """Read a payload field attributes-first, mapping-access fallback,
        default when neither is present (older engines / dict payloads)."""
        value = getattr(payload, field, None)
        if value is not None:
            return value
        if isinstance(payload, dict):
            return payload.get(field, default)
        return default

    async def _on_before_mvge_start(self, payload: Any) -> None:
        """Populate self-mod state, append the prompt section once, or stay
        silent (no section, no crash) when the payload carries no usable
        extension context."""
        agent_name = self._payload_get(payload, "agent_name")
        config_dir = self._payload_get(payload, "config_dir")
        runes_paths = self._payload_get(payload, "runes_paths", [])
        system_path = self._payload_get(payload, "system_path")

        runes_paths = [Path(p) for p in (runes_paths or []) if p]
        system_file = Path(system_path) if system_path else None
        if system_file is not None and not system_file.is_file():
            system_file = None
        config_dir_path = Path(config_dir) if config_dir else None

        spells_dir: Path | None = None
        # Spec §4.3/§5.3: the engine resolves the ACTIVE spells dir ONCE and
        # hands it over in the payload — the rune reads it, it does NOT
        # re-derive the discovery branch logic (one resolution, structural).
        # The config_dir/cwd heuristic below is graceful degradation for
        # hosts predating the payload field, not a second resolution.
        payload_spells_dir = self._payload_get(payload, "spells_dir")
        if payload_spells_dir:
            candidate_dir = Path(payload_spells_dir)
            if candidate_dir.is_dir():
                spells_dir = candidate_dir
        # The session's workspace root, when the payload carries it.
        cwd_value = self._payload_get(payload, "cwd")
        cwd_path = Path(cwd_value) if cwd_value else None
        if spells_dir is None and config_dir_path is not None:
            candidate = config_dir_path / "spells"
            if candidate.is_dir():
                spells_dir = candidate
        if spells_dir is None:
            # The engine's ./spells fallback, scoped to the session's cwd
            # when the payload carries one — never an unrelated process cwd.
            base = cwd_path if cwd_path is not None else Path.cwd()
            candidate = base / "spells"
            if candidate.is_dir():
                spells_dir = candidate

        self.state = SelfmodState(
            config_dir=config_dir_path,
            runes_paths=runes_paths,
            system_path=system_file,
            spells_dir=spells_dir,
            agent_name=str(agent_name) if agent_name else None,
            cwd=cwd_path,
        )

        try:
            section = build_selfmod_section(self.state.runes_paths, self.state.system_path)
        except Exception as exc:  # noqa: BLE001 — prompt assembly must never break session start
            logger.warning("selfmod-bridge: prompt section build failed: %s", exc)
            self.api.emit_event(
                "selfmod_bridge_warning", {"stage": "prompt_build", "detail": str(exc)}
            )
            return
        if not section:
            # Bypass: zero prompt overhead, but not zero signal (spec §5.3).
            logger.warning(
                "selfmod-bridge: prompt section skipped — no runes paths or "
                "system instructions resolved"
            )
            self.api.emit_event(
                "selfmod_bridge_warning",
                {
                    "stage": "bypass",
                    "detail": "no runes paths or system instructions resolved",
                },
            )
            return
        current = self._payload_get(payload, "base_prompt") or ""
        # Exactly once: idempotent across rehydration passes. Guard on the
        # normative section marker itself, not a content line — a bare
        # "- Runes: " string could appear in unrelated prose.
        if "\nSelf-Modification & Customization:\n" not in current:
            self._set_payload_prompt(payload, current + "\n\n" + section)

    def _set_payload_prompt(self, payload: Any, prompt: str) -> None:
        """Write the prompt back attributes-first, mapping fallback; if the
        payload shape is unknown, pass it through opaque (no prompt edit)."""
        if hasattr(payload, "base_prompt"):
            payload.base_prompt = prompt
            return
        if isinstance(payload, dict):
            payload["base_prompt"] = prompt
            return
        logger.warning(
            "selfmod-bridge: unknown before_mvge_start payload shape "
            "(%s) — passing through without prompt edit",
            type(payload).__name__,
        )
        self.api.emit_event(
            "selfmod_bridge_warning",
            {"stage": "payload_shape", "detail": type(payload).__name__},
        )

    # -- command --------------------------------------------------------

    async def handle_selfmod_command(self, args_str: str = "") -> str:
        """``/selfmod status`` (default) / ``/selfmod show`` — debug-only
        visibility. Shares ``describe_extensions`` / ``build_selfmod_section``
        with the spell and the hook: one implementation, three surfaces."""
        args = (args_str or "").strip().split()
        verb = args[0] if args else "status"
        if verb == "show":
            if self.state is None:
                return "selfmod state not initialized"
            section = build_selfmod_section(self.state.runes_paths, self.state.system_path)
            return section or "(no self-mod section resolves)"
        if verb == "status":
            if self.state is None:
                return "selfmod state not initialized"
            status = describe_extensions(self.state)
            lines = ["selfmod-bridge status:"]
            lines.append(f"  agent: {status['agent_name']}")
            lines.append(f"  config dir: {status['config_dir']}")
            lines.append(
                f"  spells dir: {status['spells_dir']} "
                f"(AGENTS.md: {'yes' if status['spells_dir_agents_md'] else 'no'})"
            )
            lines.append("  runes paths:")
            for rp in status["runes_paths"]:
                lines.append(
                    f"    - {rp['path']} (AGENTS.md: {'yes' if rp['agents_md'] else 'no'})"
                )
            lines.append(
                f"  system instructions: {status['system_path']} "
                f"({'exists' if status['system_path_exists'] else 'missing'})"
            )
            snapshots = status["snapshots"]
            lines.append(f"  snapshots ({len(snapshots)}):")
            for snap in snapshots:
                lines.append(
                    f"    - {snap['id']} (label={snap['label']}, "
                    f"{snap['created_at']}, files={len(snap['files'])})"
                )
            for warning in status["warnings"]:
                lines.append(f"  warning: {warning}")
            return "\n".join(lines)
        return "usage: /selfmod [status|show]"

    # -- spell registration ----------------------------------------------

    _SPELLS: tuple[tuple[str, str, bool], ...] = (
        ("scaffold_spell", "Create a spell (.py) in the active spells dir", False),
        ("scaffold_rune", "Scaffold a full rune tree into a configured runes path", False),
        ("scaffold_skill", "Scaffold a SKILL.md skill package per skills-bridge scopes", False),
        ("extension_status", "Report resolved extension dirs, AGENTS.md presence, snapshots", True),
        ("revise_persona", "Patch the active system instructions (exactly-one-match)", False),
        ("teach", "Append/replace named-section content in the agent's instructions", False),
        ("self_snapshot", "Snapshot the self: system file, spells dir, config, manifest", True),
        ("self_rollback", "Restore a snapshot (pre-rollback snapshot first)", False),
    )

    # Mutating ops whose outcomes are audit-logged through the engine-owned
    # rune-op audit trail (successes and structured failures alike).
    # ``extension_status`` is read-only and stays unaudited.
    _AUDITED_OPS = frozenset(
        {
            "scaffold_spell",
            "scaffold_rune",
            "scaffold_skill",
            "revise_persona",
            "teach",
            "self_snapshot",
            "self_rollback",
        }
    )

    def _spell_definitions(self) -> list[SpellDefinition]:
        return [
            SpellDefinition(
                name=name,
                description=description,
                parameters=_SPELL_PARAMETERS[name],
                handler=(
                    self._with_audit(name, getattr(self, name))
                    if name in self._AUDITED_OPS
                    else getattr(self, name)
                ),
                read_only=read_only,
                execution_mode=ExecutionMode.PARALLEL,
            )
            for name, description, read_only in self._SPELLS
        ]

    # -- registration ------------------------------------------------------

    def register(self) -> None:
        # Idempotent: the loader factory registers on construction, and
        # callers that register explicitly (tests, ad-hoc hosts) must not
        # double-register hook handlers.
        if self._registered:
            return
        self._registered = True
        self.api.on(SigilHook.BEFORE_MVGE_START, self._on_before_mvge_start)
        self.api.register_command(
            "selfmod",
            "Show selfmod-bridge status and the resolved self-mod prompt section",
            self.handle_selfmod_command,
        )
        self.api.widen_global_allowlist([name for name, _, _ in self._SPELLS])
        for spell in self._spell_definitions():
            self.api.register_spell(spell)


def create_rune(api: Any, manifest: dict[str, Any]) -> SelfmodBridgeRune:
    """Explicit factory: rune instance with a known manifest."""
    return SelfmodBridgeRune(api, manifest)


def rune_factory(api: Any, manifest: dict[str, Any] | None = None) -> SelfmodBridgeRune:
    """Loader entry point.

    The engine instantiates runes as ``factory(api)`` (runner.py:667,
    watcher.py:135) — one positional arg, no manifest. The manifest is
    therefore optional here; ``create_rune`` stays the explicit two-arg
    factory for direct construction.

    Registration happens here (not in a separate step) because the engine
    never calls ``register()`` itself — the factory is the only hook the
    loader invokes. This matches the skills-bridge convention.
    """
    rune = SelfmodBridgeRune(api, manifest if manifest is not None else {"version": "0.1.0"})
    rune.register()
    return rune
