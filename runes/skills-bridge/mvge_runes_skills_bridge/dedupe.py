from __future__ import annotations

import shutil
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from mvge_runes_skills_bridge.loader import (
    get_prioritized_skill_search_paths,
    load_skills_from_paths,
)
from mvge_runes_skills_bridge.parser import resolve_skill_file
from mvge_runes_skills_bridge.types import (
    SkillDiagnosticKind,
    SkillScope,
)

PATH_MAX_WIDTH = 50


@dataclass(frozen=True)
class ShadowedSkill:
    """A skill copy shadowed by a higher-precedence scope."""

    name: str
    shadow_scope: SkillScope
    shadow_dir: Path
    winner_scope: SkillScope
    winner_dir: Path


def plan_skill_dedupe(
    agent_name: str = "",
    cwd: Path | None = None,
    global_dir: Path | None = None,
    skill_paths: list[tuple[Path, SkillScope]] | None = None,
) -> list[ShadowedSkill]:
    """Plan removal of skill copies shadowed by a higher-precedence scope."""
    paths = (
        list(skill_paths)
        if skill_paths is not None
        else list(
            get_prioritized_skill_search_paths(
                agent_name, cwd=cwd, global_dir=global_dir
            )
        )
    )
    loads, diagnostics = load_skills_from_paths(paths, agent_name)
    winners = {load.manifest.name: load.manifest for load in loads}

    plan: list[ShadowedSkill] = []
    for diag in diagnostics:
        if diag.kind != SkillDiagnosticKind.SHADOWED_SKILL:
            continue
        if diag.scope is None:
            continue
        winner = winners.get(diag.skill_name)
        if winner is None:
            continue

        diag_path = Path(diag.path)
        shadow_dir = (
            diag_path
            if resolve_skill_file(diag_path) is not None
            else diag_path / diag.skill_name
        )

        plan.append(
            ShadowedSkill(
                name=diag.skill_name,
                shadow_scope=diag.scope,
                shadow_dir=shadow_dir,
                winner_scope=winner.scope,
                winner_dir=Path(winner.path),
            )
        )
    return plan


def execute_skill_dedupe(
    plan: list[ShadowedSkill],
    dry_run: bool = False,
) -> list[Path]:
    """Remove shadowed skill directories."""
    removed: list[Path] = []
    for entry in plan:
        if entry.shadow_dir.is_dir():
            if not dry_run:
                shutil.rmtree(entry.shadow_dir)
            removed.append(entry.shadow_dir)
    return removed


def render_dedupe_plan_table(plan: list[ShadowedSkill]) -> str:
    """Render dedupe plan using strictly ASCII box characters for cp1252 Windows safety."""
    out = StringIO()
    render_console = Console(file=out, width=200, record=True, force_terminal=False)
    table = Table(title="Shadowed Skills", box=box.ASCII)
    table.add_column("Skill")
    table.add_column("Shadowed Scope")
    table.add_column("Shadowed Path", width=PATH_MAX_WIDTH)
    table.add_column("Winner Scope")
    table.add_column("Winner Path", width=PATH_MAX_WIDTH)

    for entry in plan:
        table.add_row(
            entry.name,
            entry.shadow_scope.value,
            str(entry.shadow_dir),
            entry.winner_scope.value,
            str(entry.winner_dir),
        )

    render_console.print(table)
    return out.getvalue()
