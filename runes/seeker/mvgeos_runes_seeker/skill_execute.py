from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mvgeos_core.abort import AbortSignal
from mvgeos_core.spells import MvgeSpell, SpellContext, SpellExecutionMode
from mvgeos_provider.registry import RealmRegistry

from .dci_matcher import _get_skill_roots
from .ledger import SeekerLedger


class SkillExecuteSpell(MvgeSpell):
    """Execute a discovered skill's scripts."""

    def __init__(
        self,
        provider_registry: RealmRegistry,
        skill_dirs: list[Path] | None = None,
        agent_name: str | None = None,
        rg_timeout: int = 15,
        nlt_model: str = "openrouter/free",
        nlt_api_key: str = "",
        project_dir: str | Path | None = None,
        ledger: SeekerLedger | None = None,
        name: str = "execute",
    ) -> None:
        super().__init__(
            name=name,
            description="Execute a script from an activated skill's scripts/ directory.",
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name or directory name of the activated skill",
                    },
                    "script": {
                        "type": "string",
                        "description": "Script filename under scripts/ to execute (e.g. 'helper.py')",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional CLI arguments to pass to the script",
                    },
                },
                "required": ["skill_name", "script"],
            },
            execution_mode=SpellExecutionMode.SEQUENTIAL,
        )
        self._provider_registry = provider_registry
        self._skill_dirs = skill_dirs
        self._agent_name = agent_name
        self._project_dir = project_dir
        self._rg_timeout = rg_timeout
        self._nlt_model = nlt_model
        self._nlt_api_key = nlt_api_key
        self._ledger = ledger

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        skill_name = params.get("skill_name", "").strip()
        script = params.get("script", "").strip()
        args = params.get("args", [])
        if not skill_name or not script:
            return {"status": "error", "error": "skill_name and script are required"}

        # Activate-First Gate
        if self._ledger is not None and skill_name not in self._ledger.activated_skills:
            return {
                "status": "error",
                "error": f"Skill '{skill_name}' must be activated via 'activate' before executing its scripts.",
            }

        if ".." in Path(script).parts or Path(script).is_absolute():
            return {"status": "error", "error": "Path traversal forbidden in script"}

        roots = _get_skill_roots(self._skill_dirs, self._project_dir)
        target_script: Path | None = None
        target_skill_dir: Path | None = None

        for root in roots:
            cand = root / skill_name
            if cand.is_dir():
                sfile = cand / "scripts" / script
                if sfile.is_file():
                    target_script = sfile
                    target_skill_dir = cand
                    break

        if not target_script or not target_skill_dir:
            return {
                "status": "error",
                "error": f"Script '{script}' not found in skill '{skill_name}/scripts'",
            }

        cmd: list[str]
        if target_script.suffix == ".py":
            cmd = [sys.executable, str(target_script)]
        elif target_script.suffix == ".sh":
            cmd = ["bash", str(target_script)]
        else:
            cmd = [str(target_script)]
        cmd.extend(str(a) for a in args)

        # Check for injected SpellContext backend (Phase 1)
        ctx = kwargs.get("ctx") or kwargs.get("context")
        backend = getattr(ctx, "backend", None) if ctx else None
        if backend is not None and hasattr(backend, "exec_command"):
            try:
                res = await backend.exec_command(
                    cmd, cwd=str(target_skill_dir), timeout=30.0
                )
                return {
                    "status": "success" if res.exit_code == 0 else "failure",
                    "exit_code": res.exit_code,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                }
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "error": str(e)}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(target_skill_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=30.0
            )
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            return {
                "status": "success" if proc.returncode == 0 else "failure",
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"status": "error", "error": "Execution timed out"}
        except Exception as e:  # noqa: BLE001 -- spell returns an error payload instead of raising
            return {"status": "error", "error": str(e)}


ExecuteSpell = SkillExecuteSpell
