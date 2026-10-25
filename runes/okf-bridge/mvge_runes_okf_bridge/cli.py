"""Typer CLI interface for okf-bridge rune."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from mvge_runes_okf_bridge.graph import (
    KnowledgeGraph,
    workspace_knowledge_root,
)
from mvge_runes_okf_bridge.migrator import migrate_bundle_in_place
from mvge_runes_okf_bridge.types import ValidationIssue
from mvge_runes_okf_bridge.validator import validate_okf_bundle
from mvge_runes_okf_bridge.visualizer import generate_html_graph

app = typer.Typer(name="okf", help="Open Knowledge Format (OKF v0.2) management")


def _load_graph(bundle_dir: str | None) -> KnowledgeGraph:
    """Load an explicit bundle dir, else the merged global+workspace layers."""
    if bundle_dir:
        target = Path(bundle_dir)
        if not target.is_dir():
            typer.echo(f"MISSING: OKF directory '{target}' does not exist.")
            raise typer.Exit(1)
        return KnowledgeGraph.load(bundle_path=target)
    graph = KnowledgeGraph.load(cwd=Path.cwd())
    if not graph.bundle_layers:
        typer.echo(
            "MISSING: No knowledge bundle found "
            "(.agents/knowledge/ in cwd or $MVGE_GLOBAL_DIR)."
        )
        raise typer.Exit(1)
    return graph


@app.command("status")
def okf_status(
    bundle_dir: str = typer.Argument(None, help="Path to OKF bundle directory"),
) -> None:
    """Display summary of OKF knowledge bundle."""
    graph = _load_graph(bundle_dir)
    trust = graph.trust_summary()
    stale = graph.stale_count()
    types_str = (
        ", ".join(f"{k}: {v}" for k, v in sorted(graph.types_summary().items()))
        or "None"
    )

    typer.echo("OKF Knowledge Bundle Status:")
    typer.echo(f"  Directory:       {graph.bundle_root}")
    typer.echo(f"  Total Concepts:  {len(graph.concepts)}")
    typer.echo(f"  Types Breakdown: {types_str}")
    typer.echo(f"  Human-Reviewed:  {trust['human-reviewed']}")
    typer.echo(f"  Machine-Confirmed: {trust['machine-confirmed']}")
    typer.echo(f"  Unverified:      {trust['unverified']}")
    typer.echo(f"  Stale Concepts:  {stale}")


@app.command("validate")
def okf_validate(
    bundle_dir: str = typer.Argument(None, help="Path to OKF bundle directory"),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
) -> None:
    """Validate OKF bundle against v0.2 specification (§11)."""
    graph = _load_graph(bundle_dir)

    merged_valid = True
    total_concepts = total_indexes = total_logs = 0
    all_errors: list[ValidationIssue] = []
    all_warnings: list[ValidationIssue] = []
    for layer in graph.bundle_layers:
        report = validate_okf_bundle(layer, strict=strict)
        merged_valid = merged_valid and report.valid
        total_concepts += report.concepts
        total_indexes += report.indexes
        total_logs += report.logs
        all_errors.extend(report.errors)
        all_warnings.extend(report.warnings)

    if output_json:
        data = {
            "valid": merged_valid,
            "errors": [{"path": e.rel_path, "message": e.message} for e in all_errors],
            "warnings": [
                {"path": w.rel_path, "message": w.message} for w in all_warnings
            ],
            "concepts": total_concepts,
            "indexes": total_indexes,
            "logs": total_logs,
        }
        typer.echo(json.dumps(data, indent=2))
        if not merged_valid:
            raise typer.Exit(1)
        return

    typer.echo(
        f"Validated {total_concepts} concepts, {total_indexes} indexes, {total_logs} logs."
    )
    for err in all_errors:
        typer.echo(f"  FAIL: {err.rel_path}: {err.message}")
    for warn in all_warnings:
        typer.echo(f"  WARN: {warn.rel_path}: {warn.message}")

    if merged_valid:
        typer.echo("OK: OKF bundle is fully conformant.")
    else:
        typer.echo(f"FAIL: OKF bundle has {len(all_errors)} conformance errors.")
        raise typer.Exit(1)


@app.command("graph")
def okf_graph(
    bundle_dir: str = typer.Argument(None, help="Path to OKF bundle directory"),
    output_file: str = typer.Option(
        None, "-o", "--output", help="Output HTML file path"
    ),
) -> None:
    """Generate interactive Cytoscape.js HTML visualization of knowledge bundle."""
    graph = _load_graph(bundle_dir)
    target = Path(bundle_dir) if bundle_dir else (graph.bundle_root or Path.cwd())

    html = generate_html_graph(graph, title=f"Knowledge Graph: {target.name}")

    out_path = Path(output_file) if output_file else (target / "viz.html")
    out_path.write_text(html, encoding="utf-8")
    typer.echo(f"OK: Rendered knowledge graph to {out_path.resolve()}.")


@app.command("migrate")
def okf_migrate(
    bundle_dir: str = typer.Argument(None, help="Path to OKF bundle directory"),
) -> None:
    """Migrate an OKF v0.1 bundle to v0.2 in place (§13.1)."""
    graph = _load_graph(bundle_dir)
    target = Path(bundle_dir) if bundle_dir else (graph.bundle_root or Path.cwd())

    modified = migrate_bundle_in_place(target)
    if modified:
        typer.echo(f"OK: Migrated {len(modified)} files to OKF v0.2:")
        for m in modified:
            typer.echo(f"  - {m}")
    else:
        typer.echo("OK: All files already conform to OKF v0.2.")


@app.command("search")
def okf_search_cmd(
    query: str = typer.Argument("", help="Search query string"),
    bundle_dir: str = typer.Option(
        None, "--bundle", help="Path to OKF bundle directory"
    ),
    type_filter: str = typer.Option(None, "--type", help="Filter by concept type"),
    tag_filter: str = typer.Option(None, "--tag", help="Filter by tag"),
) -> None:
    """Search concepts in OKF knowledge bundle."""
    graph = _load_graph(bundle_dir)

    results = graph.search(query=query, type_filter=type_filter, tag_filter=tag_filter)
    if not results:
        typer.echo(f"MISSING: No concepts found matching query '{query}'.")
        return

    typer.echo(f"FOUND: {len(results)} matching concepts:")
    for c in results:
        typer.echo(
            f"  - {c.id} ({c.type}) [{c.trust_tier.value}]: {c.title or c.description}"
        )


@app.command("init")
def okf_init_cmd(
    bundle_dir: str = typer.Argument(
        None, help="Target directory for new OKF bundle (default .agents/knowledge)"
    ),
    title: str = typer.Option(
        "Project Knowledge Base", "--title", help="Title of knowledge bundle"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
) -> None:
    """Initialize a brand-new conformant OKF v0.2 bundle (§3, §8, §9)."""
    target = Path(bundle_dir) if bundle_dir else workspace_knowledge_root(Path.cwd())
    target.mkdir(parents=True, exist_ok=True)

    root_index = target / "index.md"
    root_log = target / "log.md"
    sample_concept = target / "getting-started.md"

    if not force and (root_index.exists() or root_log.exists()):
        typer.echo(
            f"FAIL: Directory '{target}' already contains OKF files. Use --force to overwrite."
        )
        raise typer.Exit(1)

    # Scaffolding root index.md
    root_index.write_text(
        f'---\nokf_version: "0.2"\n---\n\n# {title}\n\n'
        f"A conformant Open Knowledge Format (OKF v0.2) knowledge bundle.\n\n"
        f"## Concepts\n\n* [Getting Started](getting-started.md) - Overview and getting started guide.\n",
        encoding="utf-8",
    )

    # Scaffolding root log.md
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    root_log.write_text(
        f"# Directory Update Log\n\n## {today}\n* **Creation**: Initialized OKF v0.2 knowledge bundle.\n",
        encoding="utf-8",
    )

    # Scaffolding starter concept (auto-injected working concept)
    sample_concept.write_text(
        f"---\n"
        f"type: Guide\n"
        f"title: Getting Started\n"
        f"description: Overview and getting started guide for this project.\n"
        f"tags: [guide, onboarding]\n"
        f"status: stable\n"
        f"context: auto\n"
        f"generated:\n"
        f"  by: human:mvge\n"
        f"  at: '{today}'\n"
        f"verified:\n"
        f"  - by: human:mvge\n"
        f"    at: '{today}'\n"
        f"---\n\n"
        f"# Getting Started\n\nWelcome to the project knowledge base.\n",
        encoding="utf-8",
    )

    typer.echo(f"OK: Initialized conformant OKF v0.2 bundle at {target.resolve()}.")
