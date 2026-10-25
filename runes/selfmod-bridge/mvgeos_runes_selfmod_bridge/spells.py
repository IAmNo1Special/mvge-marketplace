"""Spell handlers for selfmod-bridge.

Handlers are async **bound methods** on the rune (the skills-bridge
precedent), defined here on ``SelfmodSpellsMixin`` and mixed into
``SelfmodBridgeRune`` — one home for all handler logic.

Safety contract (spec §5.6): ``scaffold_*``, ``revise_persona``, ``teach``
and ``self_rollback`` are ``read_only=False`` (fail-closed under the
approval rune; creating executable Python ungated would be a hole).
``extension_status`` and ``self_snapshot`` are ``read_only=True``. When the
approval rune is absent, ``read_only=False`` spells run ungated like any
other mutating spell — verified engine behavior; this rune implements no
private gate. Gating is the approval rune's job.

Diffs live in params, never only in results: the mutating mind-ops take
both sides explicitly (``old_text``/``new_text``) so the approval
presenter — which renders spell arguments to the *user* — shows what will
change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mvge.core import global_agents_dir, skills_dir

from mvgeos_runes_selfmod_bridge.state import SelfmodState
from mvgeos_runes_selfmod_bridge.status import describe_extensions
from mvgeos_runes_selfmod_bridge.templates import (
    rune_tree,
    skill_markdown,
    spell_source,
    spells_agents_md,
)
from mvgeos_runes_selfmod_bridge.validation import (
    ValidationError,
    validate_extension_name,
    validate_label,
    validate_scope,
    validate_target_dir,
)

logger = logging.getLogger(__name__)

RELOAD_NOTE = (
    "Takes effect on reload: Mvge.reload() rebuilds the system prompt, "
    "re-discovers the spells dir, and rehydrates rune state. Do not claim "
    "the change is live before the reload completes."
)

_STATE_NOT_INITIALIZED = "selfmod state not initialized — run /reload or start a new session"

_SNAPSHOT_DIRNAME = ".selfmod-snapshots"
_STAGING_PREFIX = ".selfmod-staging-"
_MAX_SNAPSHOTS = 20  # safety control vs disk-fill, not hygiene


class _ExistsError(Exception):
    """Internal: the scaffold target already exists (fail-closed, never overwrite)."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


class _MatchError(Exception):
    """Internal: exactly-one-match rule violated."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _exclusive_write_text(target: Path, content: str) -> bool:
    """Write ``content`` to ``target`` with exclusive-create (``O_EXCL``).

    Atomic on every platform; returns False (never overwrites) when the
    target already exists. Call via ``asyncio.to_thread`` so async handlers
    never block the event loop on file IO.
    """
    try:
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(content)
    except FileExistsError:
        return False
    return True


# Explicit JSON Schemas for the eight spells, keyed by spell name. The
# engine does not derive schemas from handler signatures — without these
# the model gets no parameter contract for the spells (adr-bridge
# precedent). ``required`` mirrors the handler behavior exactly.
_SPELL_PARAMETERS: dict[str, dict[str, Any]] = {
    "scaffold_spell": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Spell file stem: lowercase identifier, no dots, "
                    "slashes, or leading underscores."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "What the spell does — embedded as the generated module's docstring."
                ),
            },
        },
        "required": ["name"],
    },
    "scaffold_rune": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Rune directory name: lowercase identifier, no dots, "
                    "slashes, or leading underscores."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "What the rune does — embedded in the generated README, rune.py, and manifest."
                ),
            },
            "target_dir": {
                "type": "string",
                "description": (
                    "Absolute path of a configured runes dir to scaffold "
                    "into. Defaults to the first writable configured runes "
                    "path (agent > user > project scope precedence)."
                ),
            },
        },
        "required": ["name"],
    },
    "scaffold_skill": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Skill directory name: lowercase identifier, no dots, "
                    "slashes, or leading underscores."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Required — the skills-bridge loader skips SKILL.md "
                    "files with a missing or empty description."
                ),
            },
            "scope": {
                "type": "string",
                "enum": ["agent", "user", "project"],
                "default": "agent",
                "description": (
                    "Which skills dir to scaffold into (skills-bridge scope precedence)."
                ),
            },
        },
        "required": ["name", "description"],
    },
    "extension_status": {"type": "object", "properties": {}},
    "revise_persona": {
        "type": "object",
        "properties": {
            "old_text": {
                "type": "string",
                "description": (
                    "Exact text to find in the instructions file — must occur exactly once."
                ),
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text for the matched span.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Instructions file to patch. Defaults to the active system instructions file."
                ),
            },
        },
        "required": ["old_text", "new_text"],
    },
    "teach": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": ("Named section (## <section>) to append under or replace within."),
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "description": (
                    "'replace' patches one exact match; 'append' adds a "
                    "paragraph under the section header."
                ),
            },
            "old_text": {
                "type": "string",
                "description": "Replace mode: exact text to find (exactly one match).",
            },
            "new_text": {
                "type": "string",
                "description": "Replace mode: replacement text.",
            },
            "text": {
                "type": "string",
                "description": "Append mode: paragraph to add under the section header.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Instructions file to amend. Defaults to the active system instructions file."
                ),
            },
        },
        "required": ["section", "mode"],
    },
    "self_snapshot": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "Optional label for the snapshot — identifier-ish, no "
                    "path separators or leading dots."
                ),
            },
        },
    },
    "self_rollback": {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": "string",
                "description": "ID of the snapshot to restore.",
            },
        },
        "required": ["snapshot_id"],
    },
}


class SelfmodSpellsMixin:
    """All selfmod-bridge spell handlers. Mixed into ``SelfmodBridgeRune``."""

    state: SelfmodState | None
    api: Any

    # -- shared machinery -------------------------------------------------

    def _require_state(self, *, stale: bool = True) -> SelfmodState | dict[str, Any]:
        state = self.state
        if state is None:
            # Fail LOUDLY — never silently with wrong paths (spec §5.3).
            # Mutating callers carry the reload-staleness fields (a reload is
            # the recovery path); read-only callers pass stale=False.
            failure: dict[str, Any] = {
                "ok": False,
                "error": "state_not_initialized",
                "message": _STATE_NOT_INITIALIZED,
            }
            if stale:
                failure["effective_after"] = "reload"
                failure["note"] = RELOAD_NOTE
            return failure
        return state

    def _validation_error(self, exc: ValidationError, *, stale: bool = True) -> dict[str, Any]:
        if stale:
            return self._mutation_failure(exc.code, exc.message)
        return {"ok": False, "error": exc.code, "message": exc.message}

    def _mutation_failure(self, code: str, message: str, **extra: Any) -> dict[str, Any]:
        """Pinned failure shape for mutating handlers (spec §6.3).

        Every mutating result — success or failure — carries the
        reload-staleness fields so the model never implies a change is
        live before the reload completes. Read-only spells
        (``extension_status``, ``self_snapshot``) never use this helper.
        """
        return {
            "ok": False,
            "error": code,
            "message": message,
            "effective_after": "reload",
            "note": RELOAD_NOTE,
            **extra,
        }

    # -- audit trail ------------------------------------------------------

    def _with_audit(
        self,
        op: str,
        handler: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Wrap a mutating spell handler so its outcome is audit-logged.

        The wrapper keeps the ``(params, signal=None, on_update=None)``
        spell signature so ``SpellDefinition.execute`` keeps dispatching it
        the same way (first parameter named ``params``, not
        ``spell_cast_id``).
        """

        async def audited(
            params: dict[str, Any], signal: Any = None, on_update: Any = None
        ) -> dict[str, Any]:
            try:
                result = await handler(params, signal=signal, on_update=on_update)
            except Exception as exc:
                # The crash is audited best-effort, then the original
                # exception propagates unchanged — audit never masks it.
                await self._audit_crash(op, params, exc)
                raise
            return await self._audit_outcome(op, params, result)

        audited.__name__ = getattr(handler, "__name__", op)
        cast("Any", audited)._audit_op = op
        return audited

    async def _audit_outcome(
        self, op: str, params: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        """Audit one op outcome through the engine-owned log.

        Successes and structured failures are both recorded. If the audit
        write itself fails, the result becomes ``audit_failed`` — loud, and
        truthful about what the mutation did (see ``_audit_write_failure``).
        """
        if not isinstance(result, dict):
            logger.warning("op %s returned a non-dict result; skipping audit", op)
            return result
        ok = bool(result.get("ok"))
        code = "ok" if ok else str(result.get("error", "failed"))
        target = result.get("path") or result.get("snapshot_id") or params.get("name")
        message = str(result.get("message", "")) or f"{op} succeeded"
        # The snapshot id is the rollback handle — keep it queryable even
        # when the path is the primary target.
        extra: dict[str, Any] | None = None
        if isinstance(result.get("snapshot_id"), str):
            extra = {"snapshot_id": result["snapshot_id"]}
        audit = getattr(self.api, "audit", None)
        if audit is None:
            logger.warning(
                "RuneAPI.audit unavailable (host predates rune-op audit); op %s ran unaudited",
                op,
            )
            return result
        try:
            await asyncio.to_thread(
                audit,
                op,
                outcome="ok" if ok else "failed",
                code=code,
                message=message,
                target=str(target) if target is not None else None,
                extra=extra,
            )
        except Exception as exc:
            logger.exception("audit write failed for op %s", op)
            return self._audit_write_failure(op, result, ok, code, message, exc)
        return result

    @staticmethod
    def _audit_write_failure(
        op: str,
        result: dict[str, Any],
        ok: bool,
        code: str,
        message: str,
        exc: BaseException,
    ) -> dict[str, Any]:
        """Shape an ``audit_failed`` result: loud, and never a lie.

        - Mutation succeeded but is unrecorded: report failure (never a
          silent ``ok``), state plainly that the change IS live, and
          preserve the reload-staleness fields — the change still needs
          its reload.
        - Mutation failed and its audit failed too: keep ``audit_failed``
          but carry the original code/message so the root cause survives.
        """
        if ok:
            shaped: dict[str, Any] = {
                "ok": False,
                "error": "audit_failed",
                "message": (
                    f"op {op!r} completed but the audit record could not be "
                    f"written: {exc}. The change IS live but unrecorded — "
                    "verify with self_snapshot or extension_status."
                ),
            }
            for key in ("effective_after", "note", "path", "snapshot_id"):
                if key in result:
                    shaped[key] = result[key]
            return shaped
        shaped = {
            "ok": False,
            "error": "audit_failed",
            "message": (
                f"op {op!r} failed with {code!r}: {message}. Additionally, the "
                f"audit record for this failure could not be written: {exc}."
            ),
        }
        for key in ("effective_after", "note"):
            if key in result:
                shaped[key] = result[key]
        return shaped

    async def _audit_crash(self, op: str, params: dict[str, Any], exc: BaseException) -> None:
        """Best-effort audit of a handler crash; never masks the error."""
        audit = getattr(self.api, "audit", None)
        if audit is None:
            logger.warning(
                "RuneAPI.audit unavailable (host predates rune-op audit); "
                "crashed op %s ran unaudited",
                op,
            )
            return
        try:
            await asyncio.to_thread(
                audit,
                op,
                outcome="failed",
                code="exception",
                message=f"{type(exc).__name__}: {exc}",
                target=str(params.get("name")) if params.get("name") else None,
            )
        except Exception:
            logger.exception("audit write failed for crashed op %s", op)

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        """Write one file atomically: temp in the same dir + one os.replace."""
        tmp = path.parent / f".selfmod-tmp-{uuid.uuid4().hex}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    async def _stage_and_replace(
        self, parent: Path, final_name: str, files: dict[str, str]
    ) -> Path:
        """Spec §6.2: build the full tree in ``.selfmod-staging-<uuid>`` under
        ``parent``, then perform ONE ``os.replace`` onto the final name.

        Never-overwrite does NOT rely on the rename alone: on POSIX
        ``os.replace`` atomically *replaces* an existing empty directory.
        The handler explicitly verifies target non-existence immediately
        before the rename, with the instance ``asyncio.Lock`` held across
        the verify→rename window as defense-in-depth against same-process
        races (cross-process races remain a documented residual). Any
        failure removes the staging dir and the caller reports which step
        failed.
        """
        state = self.state
        assert state is not None  # callers go through _require_state
        final = parent / final_name
        if final.exists() or final.is_symlink():
            raise _ExistsError(final)
        staging = parent / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for rel, content in files.items():
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            async with state.write_lock:
                if final.exists() or final.is_symlink():
                    raise _ExistsError(final)
                os.replace(staging, final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return final

    def _snapshots_root(self, state: SelfmodState) -> Path | None:
        if state.config_dir is None:
            return None
        return state.config_dir / _SNAPSHOT_DIRNAME

    async def _snapshot_self(self, label: str | None) -> dict[str, Any]:
        """Shared snapshot mechanism (§7): one internal helper, all mutating
        mind-ops use it. Copies the resolved system_path file (if any), the
        spells dir (if any), plus manifest.json and a read-only copy of the
        agent config file (completeness only — no spell edits it).

        Write-once: never writes into an existing snapshot dir. Immediately
        durable — inert files, not live state, so no reload caveat.

        I/O failures are FAILURES, not crashes: any ``OSError`` from the
        mkdir / copy / manifest phases returns a structured
        ``{"ok": False, "error": "snapshot_failed", ...}`` result whose
        message names the failed phase and carries the underlying OS error
        (mirroring the ``scaffold_failed`` pattern), the raw traceback is
        logged via ``logger.exception``, and any half-written snapshot dir
        is removed. The ``FileExistsError`` → uuid-suffix fallback is NOT
        an error (write-once). Callers abort their mutation fail-closed on
        this result — nothing is written when the snapshot fails.
        """
        state = self.state
        assert state is not None
        if label:
            validate_label(label)
        root = self._snapshots_root(state)
        if root is None:
            return {
                "ok": False,
                "error": "no_config_dir",
                "message": "no agent config dir resolved — cannot store snapshots",
            }

        snap_dir: Path | None = None
        snapshot_id = ""
        phase = "mkdir"
        try:
            root.mkdir(parents=True, exist_ok=True)

            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            snapshot_id = f"{stamp}-{label}" if label else stamp
            snap_dir = root / snapshot_id
            try:
                snap_dir.mkdir(exist_ok=False)
            except FileExistsError:
                # Same-microsecond collision: write-once means never reuse the dir.
                snapshot_id = f"{snapshot_id}-{uuid.uuid4().hex[:8]}"
                snap_dir = root / snapshot_id
                snap_dir.mkdir(exist_ok=False)

            files: list[dict[str, str]] = []
            phase = "copy"
            # Mirror the teach targeting rule: when the hook resolved no
            # system_path but the agent-scope file exists (teach created it
            # per the spec's None rule), that file IS the instructions file
            # and must be captured — otherwise rollback on a fresh agent
            # restores nothing. Same fallback self_rollback uses for its
            # restore target, so snapshot and restore stay symmetric.
            system_file = state.system_path
            if system_file is None and state.config_dir is not None:
                candidate = state.config_dir / "SYSTEM.md"
                if candidate.is_file():
                    system_file = candidate
            if system_file is not None and system_file.is_file():
                shutil.copy2(system_file, snap_dir / "SYSTEM.md")
                files.append({"kind": "system", "name": "SYSTEM.md"})
            if state.spells_dir is not None and state.spells_dir.is_dir():
                shutil.copytree(state.spells_dir, snap_dir / "spells")
                files.append({"kind": "spells", "name": "spells"})
            config_file = state.config_dir / "config.json" if state.config_dir else None
            if config_file is not None and config_file.is_file():
                dest = snap_dir / "config.json"
                shutil.copy2(config_file, dest)
                dest.chmod(0o444)  # read-only: completeness only, no spell edits it
                files.append({"kind": "config", "name": "config.json"})

            phase = "manifest"
            manifest = {
                "created_at": datetime.now(UTC).isoformat(),
                "label": label,
                "files": files,
                "agent_name": state.agent_name,
            }
            (snap_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.exception(
                "selfmod-bridge: snapshot %s failed during %s",
                snapshot_id or label or "<unnamed>",
                phase,
            )
            if snap_dir is not None:
                shutil.rmtree(snap_dir, ignore_errors=True)
            return {
                "ok": False,
                "error": "snapshot_failed",
                "message": f"snapshot failed during {phase}: {exc}",
            }
        assert snap_dir is not None  # set before any fallible step past "mkdir"

        # Cap: keep the 20 most recent, prune the rest. This is a SAFETY
        # CONTROL against disk-fill (a snapshot per mind-op is otherwise an
        # unbounded write vector), not hygiene.
        snaps = sorted(
            (c for c in root.iterdir() if c.is_dir() and (c / "manifest.json").is_file()),
            key=lambda c: c.name,
        )
        for old in snaps[:-_MAX_SNAPSHOTS] if len(snaps) > _MAX_SNAPSHOTS else []:
            shutil.rmtree(old, ignore_errors=True)

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "path": str(snap_dir),
            "files": files,
            "note": "Snapshot is immediately durable (inert files, not live state).",
        }

    # -- creation spells --------------------------------------------------

    async def scaffold_spell(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Create ``<spells_dir>/<name>.py`` from the normative template."""
        state = self._require_state()
        if isinstance(state, dict):
            return state
        name = str(params.get("name") or "")
        description = str(params.get("description") or "")

        spells_dir = state.spells_dir
        if spells_dir is None:
            config_hint = str(state.config_dir) if state.config_dir else "<agent_config_dir>"
            return self._mutation_failure(
                "no_spells_dir",
                (
                    "no active spells dir resolved — create "
                    f"{config_hint}/spells or ./spells first, then reload"
                ),
            )
        try:
            validate_extension_name(name, spells_dir)
        except ValidationError as exc:
            return self._validation_error(exc)

        target = spells_dir / f"{name}.py"
        # Exclusive-create: atomic on every platform, never overwrites.
        created = await asyncio.to_thread(
            _exclusive_write_text, target, spell_source(name, description)
        )
        if not created:
            return self._mutation_failure(
                "exists",
                f"spell {name!r} already exists — refusing to overwrite",
                path=str(target),
            )

        # Seed the spells-dir AGENTS.md (exclusive-create, only if absent) —
        # the injected section tells the model to read it; the instruction
        # must not dangle.
        agents_md = spells_dir / "AGENTS.md"
        await asyncio.to_thread(_exclusive_write_text, agents_md, spells_agents_md())

        return {
            "ok": True,
            "path": str(target),
            "effective_after": "reload",
            "note": RELOAD_NOTE,
        }

    def _default_rune_target(self, state: SelfmodState) -> Path | dict[str, Any]:
        """Select the default runes path: scope precedence agent > user > project,
        first writable. Never implicit cwd-relative."""
        if not state.runes_paths:
            return self._mutation_failure(
                "no_runes_paths",
                "no runes paths configured — pass target_dir explicitly",
            )
        global_base: Path | None = None
        global_base = global_agents_dir()

        def scope(path: Path) -> int:
            resolved = path.resolve()
            if state.config_dir is not None and resolved.is_relative_to(state.config_dir.resolve()):
                return 0  # agent scope
            if resolved.is_relative_to(global_base.resolve()):
                return 1  # user scope
            return 2  # project scope

        for candidate in sorted(state.runes_paths, key=scope):
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        return self._mutation_failure(
            "no_runes_paths",
            "none of the configured runes paths is writable",
        )

    async def scaffold_rune(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Scaffold a full rune tree atomically (§6.2) into a configured runes path."""
        state = self._require_state()
        if isinstance(state, dict):
            return state
        name = str(params.get("name") or "")
        description = str(params.get("description") or "")
        target_dir = params.get("target_dir")

        if target_dir:
            try:
                root = validate_target_dir(target_dir, state.runes_paths)
            except ValidationError as exc:
                return self._validation_error(exc)
        else:
            default = self._default_rune_target(state)
            if isinstance(default, dict):
                return default
            root = default

        try:
            validate_extension_name(name, root)
        except ValidationError as exc:
            return self._validation_error(exc)

        tree = {rel: content for rel, content in rune_tree(name, description).items()}
        try:
            final = await self._stage_and_replace(root, name, tree)
        except _ExistsError as exc:
            return self._mutation_failure(
                "exists",
                f"rune {name!r} already exists — refusing to overwrite",
                path=str(exc.path),
            )
        except OSError as exc:
            return self._mutation_failure(
                "scaffold_failed",
                f"scaffold failed during staging/rename: {exc}",
                target_dir=str(root),
            )

        return {
            "ok": True,
            "path": str(final),
            "target_dir": str(root),
            "effective_after": "reload",
            "note": ("Scaffolded runes hot-reload via the rune watcher. " + RELOAD_NOTE),
        }

    def _skill_root(self, state: SelfmodState, scope: str) -> Path | dict[str, Any]:
        """Resolve the skills-bridge scope root verbatim (spec §5.4/§6.5).

        PROJECT (``<cwd>/.agents/skills/``, ``<cwd>/skills/``) >
        USER (``~/.agents/skills/`` honoring ``$MVGEOS_GLOBAL_DIR``) >
        AGENT (``<config_dir>/skills/``). Only existing dirs are discovered —
        the root is never invented here.
        """
        if scope == "project":
            if state.cwd is None:
                return self._mutation_failure(
                    "no_skills_dir",
                    "no cwd resolved for project scope",
                    scope=scope,
                )
            candidates = [
                state.cwd / ".agents" / "skills",
                state.cwd / "skills",
            ]
            for cand in candidates:
                if cand.is_dir():
                    return cand
            return self._mutation_failure(
                "no_skills_dir",
                "no project skills dir exists — the loader only discovers "
                "existing dirs; create one first",
                scope=scope,
                expected=[str(c) for c in candidates],
            )
        if scope == "user":
            root = skills_dir()
        else:  # agent
            if state.config_dir is None:
                return self._mutation_failure(
                    "no_skills_dir",
                    "no agent config dir resolved",
                    scope=scope,
                )
            root = state.config_dir / "skills"
        if not root.is_dir():
            return self._mutation_failure(
                "no_skills_dir",
                f"no {scope} skills dir at {root} — the loader only "
                "discovers existing dirs; create it first",
                scope=scope,
                expected=str(root),
            )
        return root

    def _skill_shadow_warnings(
        self, state: SelfmodState, scope: str, name: str, chosen: Path
    ) -> list[str]:
        """Warn when a higher-precedence scope already has this skill name
        (established shadowing rule: higher scopes shadow lower ones)."""
        order = ["project", "user", "agent"]
        higher = order[: order.index(scope)]
        warnings: list[str] = []
        for other in higher:
            root = self._skill_root(state, other)
            if isinstance(root, dict) or root == chosen:
                continue
            if (root / name).is_dir():
                warnings.append(
                    f"a {other}-scope skill named {name!r} already exists at "
                    f"{root / name} and will shadow this one"
                )
        return warnings

    async def scaffold_skill(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Scaffold ``<root>/<name>/SKILL.md`` per the skills-bridge scope precedent."""
        state = self._require_state()
        if isinstance(state, dict):
            return state
        name = str(params.get("name") or "")
        description = params.get("description")
        scope = str(params.get("scope") or "agent")

        if not isinstance(description, str) or not description.strip():
            # The skills-bridge loader strictly skips SKILL.md with
            # missing/empty description — without this the spell emits
            # dead-on-arrival skills.
            return self._mutation_failure(
                "missing_description",
                "description is required: the skills-bridge loader "
                "skips skills with missing/empty descriptions",
            )
        try:
            validate_scope(scope)
        except ValidationError as exc:
            return self._validation_error(exc)

        root = self._skill_root(state, scope)
        if isinstance(root, dict):
            return root
        try:
            validate_extension_name(name, root)
        except ValidationError as exc:
            return self._validation_error(exc)

        files = {"SKILL.md": skill_markdown(name, description)}
        try:
            final = await self._stage_and_replace(root, name, files)
        except _ExistsError as exc:
            return self._mutation_failure(
                "exists",
                f"skill {name!r} already exists — refusing to overwrite",
                path=str(exc.path),
            )
        except OSError as exc:
            return self._mutation_failure(
                "scaffold_failed",
                f"scaffold failed during staging/rename: {exc}",
                target_dir=str(root),
            )

        result: dict[str, Any] = {
            "ok": True,
            "path": str(final),
            "scope": scope,
            "effective_after": "reload",
            "note": RELOAD_NOTE,
        }
        warnings = self._skill_shadow_warnings(state, scope, name, root)
        if warnings:
            result["warnings"] = warnings
        return result

    async def extension_status(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Read-only: resolved dirs, AGENTS.md presence, warnings, snapshots."""
        state = self._require_state(stale=False)
        if isinstance(state, dict):
            return state
        return describe_extensions(state)

    # -- mind-ops ----------------------------------------------------------

    @staticmethod
    def _exactly_one_match(content: str, old_text: str) -> None:
        count = content.count(old_text)
        if count == 0:
            raise _MatchError("no_match", "old_text not found in the target file")
        if count > 1:
            raise _MatchError(
                "ambiguous_match",
                f"old_text matches {count} times — refusing to guess which one",
            )

    def _resolve_mind_file(
        self, state: SelfmodState, path: str | None, *, create_agent_scope: bool
    ) -> Path | dict[str, Any]:
        """One targeting rule for revise_persona and teach: default to the
        state's resolved system_path — the file actually shaping the
        session. A hardcoded <config_dir>/SYSTEM.md would silently no-op
        when a project-level file shadows it.

        When system_path is None: revise_persona fails cleanly (never invent
        a persona file); teach creates the agent-scope <config_dir>/SYSTEM.md
        on demand (per the system-prompt discovery chain — agent scope is
        the lowest file-backed layer).
        """
        if path:
            return Path(path)
        if state.system_path is not None:
            return state.system_path
        if not create_agent_scope:
            return self._mutation_failure(
                "no_system_path",
                "no system instructions file resolved (built-in default "
                "is active) — refusing to invent a persona file",
            )
        if state.config_dir is None:
            return self._mutation_failure(
                "no_system_path",
                "no system instructions file and no config dir resolved",
            )
        return state.config_dir / "SYSTEM.md"

    async def revise_persona(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Patch mode ONLY: replace exactly one occurrence of old_text with
        new_text. Never appends, never manages sections (teach owns that),
        never replaces the whole file (both sides ride in params, §5.6)."""
        state = self._require_state()
        if isinstance(state, dict):
            return state
        old_text = params.get("old_text")
        new_text = params.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            return self._mutation_failure(
                "missing_old_text",
                "old_text is required — diffs live in params, not results",
            )
        if not isinstance(new_text, str):
            return self._mutation_failure(
                "missing_new_text",
                "new_text is required — diffs live in params, not results",
            )

        target = self._resolve_mind_file(state, params.get("path"), create_agent_scope=False)
        if isinstance(target, dict):
            return target
        if not target.is_file():
            return self._mutation_failure(
                "no_match",
                f"target file does not exist: {target}",
            )
        content = target.read_text(encoding="utf-8")
        try:
            self._exactly_one_match(content, old_text)
        except _MatchError as exc:
            return self._mutation_failure(exc.code, str(exc))

        async with state.write_lock:
            snapshot = await self._snapshot_self("revise-persona")
            if not snapshot.get("ok"):
                return self._mutation_failure(
                    str(snapshot.get("error", "snapshot_failed")),
                    str(snapshot.get("message", "pre-edit snapshot failed")),
                )
            new_content = content.replace(old_text, new_text, 1)
            self._atomic_write_bytes(target, new_content.encode("utf-8"))

        return {
            "ok": True,
            "path": str(target),
            "snapshot_id": snapshot["snapshot_id"],
            "effective_after": "reload",
            "note": RELOAD_NOTE,
        }

    @staticmethod
    def _append_under_header(content: str, section: str, text: str) -> tuple[str, bool]:
        """Append ``text`` as a paragraph under the ``## <section>`` header.

        The header is created at EOF when missing — never duplicated.
        Bloat control: an identical paragraph already present in the
        section is a no-op (named sections must not grow duplicates on
        repeated teaches).
        """
        header = f"## {section}"
        para = text.strip()
        if not para:
            return content, False
        lines = content.splitlines()
        try:
            h = next(i for i, line in enumerate(lines) if line.strip() == header)
        except StopIteration:
            base = content.rstrip("\n")
            new = (base + "\n\n" if base else "") + f"{header}\n\n{para}\n"
            return new, True
        end = len(lines)
        for i in range(h + 1, len(lines)):
            if lines[i].startswith("## "):
                end = i
                break
        body = "\n".join(lines[h + 1 : end])
        if para in body:
            return content, False
        before = lines[:end]
        while before and not before[-1].strip():
            before.pop()
        new_lines = before + ["", para] + lines[end:]
        return "\n".join(new_lines) + "\n", True

    async def teach(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Owns append + named-section lifecycle for the agent's own
        instructions. ``replace`` patches with the exactly-one-match rule;
        ``append`` adds under the named section header."""
        state = self._require_state()
        if isinstance(state, dict):
            return state
        section = str(params.get("section") or "")
        mode = str(params.get("mode") or "")
        if not section:
            return self._mutation_failure(
                "missing_section",
                "section is required — teach operates on named sections",
            )
        if mode not in ("replace", "append"):
            return self._mutation_failure(
                "invalid_mode",
                f"invalid mode {mode!r}: must be 'replace' or 'append'",
            )

        target = self._resolve_mind_file(state, params.get("path"), create_agent_scope=True)
        if isinstance(target, dict):
            return target

        content = target.read_text(encoding="utf-8") if target.is_file() else ""
        try:
            if mode == "replace":
                old_text = params.get("old_text") or ""
                new_text = params.get("new_text") or ""
                if not old_text:
                    return self._mutation_failure(
                        "missing_old_text",
                        "old_text is required in replace mode",
                    )
                self._exactly_one_match(content, old_text)
                new_content = content.replace(old_text, new_text, 1)
                changed = True
            else:
                new_content, changed = self._append_under_header(
                    content, section, str(params.get("text") or "")
                )
        except _MatchError as exc:
            return self._mutation_failure(exc.code, str(exc))

        if not changed:
            return {
                "ok": True,
                "path": str(target),
                "mode": mode,
                "changed": False,
                "effective_after": "reload",
                "note": "no change — the content is already present",
            }

        async with state.write_lock:
            snapshot = await self._snapshot_self("teach")
            if not snapshot.get("ok"):
                return self._mutation_failure(
                    str(snapshot.get("error", "snapshot_failed")),
                    str(snapshot.get("message", "pre-edit snapshot failed")),
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write_bytes(target, new_content.encode("utf-8"))

        result: dict[str, Any] = {
            "ok": True,
            "path": str(target),
            "mode": mode,
            "changed": True,
            "snapshot_id": snapshot["snapshot_id"],
            "effective_after": "reload",
            "note": RELOAD_NOTE,
        }
        if state.system_path is not None and target.resolve() != state.system_path.resolve():
            # Mirrors the skills-bridge SHADOWED_SKILL rule: the written
            # file is not the active instructions file.
            result["warning"] = (
                f"wrote {target} but the active system instructions are "
                f"{state.system_path} — the active file takes precedence"
            )
        return result

    async def self_snapshot(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Snapshot the self: system file, spells dir, config file, manifest.
        Immediately durable — inert files, not live state, so no reload caveat."""
        state = self._require_state(stale=False)
        if isinstance(state, dict):
            return state
        label = params.get("label")
        if label is not None and not isinstance(label, str):
            return {
                "ok": False,
                "error": "invalid_label",
                "message": "label must be a string",
            }
        label = label or None
        try:
            if label:
                validate_label(label)
        except ValidationError as exc:
            return self._validation_error(exc, stale=False)
        return await self._snapshot_self(label)

    async def self_rollback(
        self, params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        """Restore a snapshot. Pre-rollback snapshot first (no lost states),
        restore atomically, report restored files + the pre-rollback id.

        Snapshot-then-write is not transactional across process death — a
        crash between the snapshot and the restore leaves the pre-rollback
        snapshot on disk, which is exactly what makes the partial state
        recoverable. Acceptable *because* the snapshot makes it recoverable.
        """
        state = self._require_state()
        if isinstance(state, dict):
            return state
        snapshot_id = str(params.get("snapshot_id") or "")
        root = self._snapshots_root(state)
        if root is None or not snapshot_id:
            return self._mutation_failure("unknown_snapshot", "unknown snapshot")
        # Containment BEFORE the existence check — fail closed. One code
        # covers both cases; never distinguish "not found" from "rejected".
        candidate = (root / snapshot_id).resolve()
        if not candidate.is_relative_to(root.resolve()) or not candidate.is_dir():
            return self._mutation_failure("unknown_snapshot", "unknown snapshot")
        manifest_path = candidate / "manifest.json"
        if not manifest_path.is_file():
            return self._mutation_failure("unknown_snapshot", "unknown snapshot")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            return self._mutation_failure("unknown_snapshot", "unknown snapshot")

        async with state.write_lock:
            pre = await self._snapshot_self("pre-rollback")
            if not pre.get("ok"):
                return self._mutation_failure(
                    str(pre.get("error", "snapshot_failed")),
                    str(pre.get("message", "pre-rollback snapshot failed")),
                )

            restored: list[str] = []
            skipped: list[str] = []
            for entry in manifest.get("files", []):
                kind = entry.get("kind")
                name = entry.get("name", "")
                src = candidate / name
                if kind == "system":
                    target = (
                        state.system_path
                        if state.system_path is not None
                        else (state.config_dir / "SYSTEM.md" if state.config_dir else None)
                    )
                    if target is None or not src.is_file():
                        skipped.append(f"system:{name} (no live target)")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self._atomic_write_bytes(target, src.read_bytes())
                    restored.append(str(target))
                elif kind == "spells":
                    if state.spells_dir is None or not src.is_dir():
                        skipped.append(f"spells:{name} (no active spells dir)")
                        continue
                    for item in sorted(src.rglob("*")):
                        if not item.is_file():
                            continue
                        rel = item.relative_to(src)
                        dest = state.spells_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        self._atomic_write_bytes(dest, item.read_bytes())
                        restored.append(str(dest))
                else:
                    # config.json: completeness only — no spell restores it.
                    skipped.append(f"{kind}:{name} (never restored)")

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "restored": restored,
            "skipped": skipped,
            "pre_rollback_snapshot_id": pre["snapshot_id"],
            "effective_after": "reload",
            "note": RELOAD_NOTE,
        }
