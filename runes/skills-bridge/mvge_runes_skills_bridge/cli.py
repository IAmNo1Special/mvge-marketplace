from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from mvge_runes_skills_bridge.dedupe import (
    execute_skill_dedupe,
    plan_skill_dedupe,
    render_dedupe_plan_table,
)
from mvge_runes_skills_bridge.loader import (
    discover_plugin_skill_paths,
    get_prioritized_skill_search_paths,
    load_skills_from_paths,
)
from mvge_runes_skills_bridge.parser import parse_skill_manifest
from mvge_runes_skills_bridge.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
)

console = Console(force_terminal=False)

skill_app = typer.Typer(
    name="skill",
    help="Inspect, validate, and manage agent skills (agentskills.io).",
)


@skill_app.callback(invoke_without_command=True)
def skill_callback(ctx: typer.Context) -> None:
    """Inspect and manage agent skills."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@skill_app.command("list")
def skill_list(
    agent_name: str = typer.Option(
        "", "--agent-name", help="Agent name for agent scope"
    ),
) -> None:
    """List all discovered agent skills across project, user, and plugin scopes."""
    paths = get_prioritized_skill_search_paths(agent_name)
    plugin_paths = discover_plugin_skill_paths()
    all_paths = paths + plugin_paths

    loads, diagnostics = load_skills_from_paths(all_paths, agent_name)

    if not loads:
        console.print("MISSING: No skills found in search paths.")
        return

    console.print(f"FOUND: {len(loads)} skills across {len(all_paths)} scopes:")
    for load in loads:
        m = load.manifest
        console.print(f"  - {m.name} ({m.scope.value}) -> {m.location}", markup=False)

    shadowed = [d for d in diagnostics if d.kind == SkillDiagnosticKind.SHADOWED_SKILL]
    if shadowed:
        console.print(f"\nSHADOWED: {len(shadowed)} skill copies shadowed:")
        for s in shadowed:
            console.print(
                f"  - {s.skill_name} ({s.scope.value if s.scope else 'unknown'}): {s.path}",
                markup=False,
            )


@skill_app.command("dedupe")
def skill_dedupe(
    agent_name: str = typer.Option(
        "", "--agent-name", help="Agent name for agent scope"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List shadowed skills without deleting"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Delete shadowed copies without prompt"
    ),
) -> None:
    """Remove dead skill copies shadowed by a higher-precedence scope."""
    plan = plan_skill_dedupe(agent_name)

    if not plan:
        console.print("OK: No shadowed skills found.")
        return

    table_str = render_dedupe_plan_table(plan)
    console.print(table_str)

    if dry_run:
        console.print("DRY-RUN: No files were deleted.")
        return

    if not yes:
        console.print(
            "To delete the shadowed skill copies listed above, run with --yes"
        )
        return

    removed = execute_skill_dedupe(plan)
    for p in removed:
        console.print(f"REMOVED: {p}")
    console.print(f"OK: Removed {len(removed)} shadowed skill directories.")


@skill_app.command("validate")
def skill_validate(
    path: str = typer.Argument(..., help="Path to the skill directory"),
) -> None:
    """Validate a skill directory against the agentskills.io specification."""
    p = Path(path).expanduser().resolve()
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(p, diagnostics=diagnostics, lenient=False)

    if manifest is None:
        console.print(f"FAIL: Skill at {p} is invalid:")
        for d in diagnostics:
            console.print(f"  ERROR: {d.message}")
        raise typer.Exit(1)

    console.print(f"OK: Skill '{manifest.name}' is valid.")
    if diagnostics:
        for d in diagnostics:
            console.print(f"  WARNING: {d.message}")


@skill_app.command("show")
def skill_show(
    name: str = typer.Argument(..., help="Name of the skill to inspect"),
    agent_name: str = typer.Option(
        "", "--agent-name", help="Agent name for agent scope"
    ),
) -> None:
    """Show details and body instructions for an installed skill."""
    paths = (
        get_prioritized_skill_search_paths(agent_name) + discover_plugin_skill_paths()
    )
    loads, _ = load_skills_from_paths(paths, agent_name)

    target = next((load.manifest for load in loads if load.manifest.name == name), None)
    if target is None:
        console.print(f"FAIL: Skill '{name}' not found.")
        raise typer.Exit(1)

    console.print(f"Skill: {target.name}")
    console.print(f"Description: {target.description}")
    console.print(f"Scope: {target.scope.value}")
    console.print(f"Location: {target.location}")
    if target.allowed_tools:
        console.print(f"Allowed tools: {target.allowed_tools}")
    console.print("\nInstructions:")
    console.print(target.body or "(No markdown instructions)")
