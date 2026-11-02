"""Rune factory and lifecycle hook registration for okf-bridge.

The rune owns the .agents/knowledge OKF bundle: it merges the global and
workspace layers, injects a token-budgeted working-concept block each turn,
and exposes the concept lifecycle (write, verify, deprecate, set context)
as spells the model can call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import BeforeMvgeStartData, SigilHook, SpellDefinition

from mvge_runes_okf_bridge.graph import (
    KnowledgeGraph,
    global_knowledge_root,
    workspace_knowledge_root,
)
from mvge_runes_okf_bridge.prompt import (
    WORKING_CONCEPTS_TOKEN_BUDGET,
    render_working_concepts,
    update_invocations_with_concepts,
)
from mvge_runes_okf_bridge.validator import validate_okf_bundle
from mvge_runes_okf_bridge.visualizer import generate_html_graph
from mvge_runes_okf_bridge.writer import (
    CONTEXT_VALUES,
    DEFAULT_GENERATED_BY,
    deprecate_concept,
    set_concept_context,
    verify_concept,
    write_concept,
)


def _merged_validation_report(graph: KnowledgeGraph) -> dict[str, Any]:
    """Validate every loaded layer and merge the reports."""
    merged_errors: list[dict[str, str]] = []
    merged_warnings: list[dict[str, str]] = []
    concepts = indexes = logs = 0
    valid = True
    for layer in graph.bundle_layers:
        report = validate_okf_bundle(layer)
        valid = valid and report.valid
        concepts += report.concepts
        indexes += report.indexes
        logs += report.logs
        merged_errors.extend(
            {"path": e.rel_path, "message": e.message} for e in report.errors
        )
        merged_warnings.extend(
            {"path": w.rel_path, "message": w.message} for w in report.warnings
        )
    return {
        "valid": valid,
        "errors": merged_errors,
        "warnings": merged_warnings,
        "concepts_checked": concepts,
        "indexes_checked": indexes,
        "logs_checked": logs,
    }


def rune_factory(api: RuneAPI) -> None:
    """Initialize and bind OKF bridge rune to session lifecycle."""
    ctx = api.context
    raw_cwd = getattr(ctx, "cwd", None) if ctx else None
    cwd: Path | None = Path(raw_cwd) if raw_cwd else None

    # In-memory merged knowledge graph (global + workspace layers)
    graph = KnowledgeGraph.load(cwd=cwd)
    concepts_xml = render_working_concepts(graph)

    # Model writes land in the workspace layer (created on demand).
    def write_root() -> Path:
        return workspace_knowledge_root(cwd) if cwd else global_knowledge_root()

    def refresh() -> None:
        nonlocal graph, concepts_xml
        graph = KnowledgeGraph.load(cwd=cwd)
        concepts_xml = render_working_concepts(graph)

    # 1. Lifecycle Hooks
    async def on_session_start(_data: Any = None) -> None:
        refresh()

    async def on_before_mvge_start(data: Any = None) -> Any:
        if not concepts_xml:
            return data

        if isinstance(data, BeforeMvgeStartData):
            data.base_prompt = f"{data.base_prompt}\n\n{concepts_xml}".strip()
            return data
        if isinstance(data, dict) and "base_prompt" in data:
            data["base_prompt"] = f"{data['base_prompt']}\n\n{concepts_xml}".strip()
            return data
        return data

    async def on_context_transform(invocations: Any = None) -> Any:
        if isinstance(invocations, list):
            return update_invocations_with_concepts(invocations, concepts_xml)
        return invocations

    async def on_session_shutdown(_data: Any = None) -> None:
        nonlocal graph, concepts_xml
        graph = KnowledgeGraph()
        concepts_xml = ""

    api.on(SigilHook.SESSION_START, on_session_start)
    api.on(SigilHook.BEFORE_MVGE_START, on_before_mvge_start)
    api.on(SigilHook.CONTEXT_TRANSFORM, on_context_transform)
    api.on(SigilHook.SESSION_SHUTDOWN, on_session_shutdown)

    # 2. Spells
    async def handle_concept_search(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        q = str(payload.get("query", ""))
        type_filter = payload.get("type_filter")
        tag_filter = payload.get("tag_filter")

        results = graph.search(
            query=q,
            type_filter=str(type_filter) if type_filter else None,
            tag_filter=str(tag_filter) if tag_filter else None,
        )
        data = [
            {
                "id": c.id,
                "title": c.title or c.id,
                "type": c.type,
                "description": c.description,
                "trust_tier": c.trust_tier.value,
                "is_stale": c.is_stale,
                "tags": c.tags,
                "origin": c.origin,
            }
            for c in results
        ]
        return json.dumps(data, indent=2)

    async def handle_concept_get(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        cid = str(payload.get("concept_id", "")).strip()
        concept = graph.get(cid)
        if concept is None:
            return json.dumps(
                {"error": f"Concept '{cid}' not found in knowledge bundle."}
            )

        data = {
            "id": concept.id,
            "type": concept.type,
            "title": concept.title,
            "description": concept.description,
            "resource": concept.resource,
            "tags": concept.tags,
            "status": concept.status,
            "stale_after": concept.stale_after,
            "context": concept.context,
            "origin": concept.origin,
            "trust_tier": concept.trust_tier.value,
            "is_stale": concept.is_stale,
            "generated": concept.generated,
            "verified": concept.verified,
            "sources": [
                {
                    "id": s.id,
                    "resource": s.resource,
                    "title": s.title,
                    "author": s.author,
                    "usage_count": s.usage_count,
                    "last_modified": s.last_modified,
                }
                for s in concept.sources
            ],
            "links_to": graph.links_to(concept.id),
            "cited_by": graph.cited_by(concept.id),
            "body": concept.body,
        }
        return json.dumps(data, indent=2)

    async def handle_concept_validate(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        target = payload.get("bundle_path")

        if target:
            report = validate_okf_bundle(Path(str(target)))
            data = {
                "valid": report.valid,
                "errors": [
                    {"path": e.rel_path, "message": e.message} for e in report.errors
                ],
                "warnings": [
                    {"path": w.rel_path, "message": w.message} for w in report.warnings
                ],
                "concepts_checked": report.concepts,
                "indexes_checked": report.indexes,
                "logs_checked": report.logs,
            }
            return json.dumps(data, indent=2)
        return json.dumps(_merged_validation_report(graph), indent=2)

    async def handle_concept_write(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        try:
            concept = write_concept(
                write_root(),
                str(payload.get("concept_id", "")),
                type=str(payload.get("type", "")),
                title=str(payload.get("title", "")),
                description=str(payload.get("description", "")),
                body=str(payload.get("body", "")),
                tags=list(payload.get("tags", []) or []),
                context=payload.get("context"),
                stale_after=payload.get("stale_after"),
                resource=payload.get("resource"),
                sources=payload.get("sources"),
                generated_by=str(payload.get("by", "") or DEFAULT_GENERATED_BY),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        refresh()
        return json.dumps(
            {
                "id": concept.id,
                "path": str(concept.path),
                "trust_tier": concept.trust_tier.value,
                "context": concept.context,
            },
            indent=2,
        )

    async def handle_concept_verify(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        try:
            concept = verify_concept(
                write_root(),
                str(payload.get("concept_id", "")),
                by=str(payload.get("by", "")),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        refresh()
        return json.dumps(
            {"id": concept.id, "trust_tier": concept.trust_tier.value}, indent=2
        )

    async def handle_concept_deprecate(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        try:
            concept = deprecate_concept(
                write_root(), str(payload.get("concept_id", ""))
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        refresh()
        return json.dumps({"id": concept.id, "status": concept.status}, indent=2)

    async def handle_concept_set_context(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        try:
            concept = set_concept_context(
                write_root(),
                str(payload.get("concept_id", "")),
                str(payload.get("context", "")),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        refresh()
        return json.dumps({"id": concept.id, "context": concept.context}, indent=2)

    api.register_spell(
        SpellDefinition(
            name="concept_search",
            description=(
                "Search the Open Knowledge Format knowledge base (global and "
                "workspace layers) by query, type, or tags. Finds both "
                "auto-injected and search-only concepts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword"},
                    "type_filter": {
                        "type": "string",
                        "description": "Filter by concept type",
                    },
                    "tag_filter": {
                        "type": "string",
                        "description": "Filter by tag",
                    },
                },
            },
            handler=handle_concept_search,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_get",
            description=(
                "Retrieve full detail, trust metadata, sources, and links for "
                "one OKF concept."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept_id": {
                        "type": "string",
                        "description": "Concept ID (relative path without .md)",
                    },
                },
                "required": ["concept_id"],
            },
            handler=handle_concept_get,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_validate",
            description=(
                "Validate the loaded OKF bundle layers against the v0.2 "
                "specification (§11). Pass bundle_path to validate one "
                "directory instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "bundle_path": {
                        "type": "string",
                        "description": "Optional path to a single bundle directory",
                    },
                },
            },
            handler=handle_concept_validate,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_write",
            description=(
                "Record durable knowledge as an OKF concept in the workspace "
                "knowledge bundle. The concept is stamped with the writing "
                "actor and held at unverified trust until a human verifies it "
                "with concept_verify. New concepts default to search-only; "
                "pass context 'auto' only for knowledge the model should see "
                "on every turn. Pass your own agent actor name as 'by'; never "
                "claim a human: actor."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept_id": {
                        "type": "string",
                        "description": "Concept ID, e.g. 'notes/my-note'",
                    },
                    "type": {
                        "type": "string",
                        "description": "Concept type (required by OKF v0.2)",
                    },
                    "title": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": (
                            "One-line summary. This is the relevance signal "
                            "for future injection: write it so a future turn "
                            "can judge relevance without opening the body."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": "Markdown body carrying the knowledge",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "context": {
                        "type": "string",
                        "enum": list(CONTEXT_VALUES),
                        "description": "auto = injected every turn (budgeted); "
                        "search-only = retrievable via search",
                    },
                    "stale_after": {
                        "type": "string",
                        "description": "ISO date after which the concept is stale",
                    },
                    "resource": {
                        "type": "string",
                        "description": (
                            "Bundle-relative path of the asset this concept "
                            "describes, e.g. 'references/diagram.png' "
                            "(OKF v0.2 §6.3)"
                        ),
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "resource": {"type": "string"},
                                "title": {"type": "string"},
                                "author": {"type": "string"},
                            },
                            "required": ["id", "resource"],
                        },
                        "description": (
                            "Provenance sources (OKF v0.2 §5.1); each needs "
                            "an id and a bundle-relative resource path"
                        ),
                    },
                    "by": {
                        "type": "string",
                        "description": "Writing actor "
                        f"(default {DEFAULT_GENERATED_BY})",
                    },
                },
                "required": ["concept_id", "type"],
            },
            handler=handle_concept_write,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_verify",
            description=(
                "Record verification of a concept, moving it up the trust "
                "ladder: unverified -> machine-confirmed -> human-reviewed "
                "(human-reviewed requires a 'human:' actor prefix)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept_id": {
                        "type": "string",
                        "description": "Concept ID (relative path without .md)",
                    },
                    "by": {
                        "type": "string",
                        "description": "Verifying actor, e.g. 'human:malcom'",
                    },
                },
                "required": ["concept_id", "by"],
            },
            handler=handle_concept_verify,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_deprecate",
            description=(
                "Mark a concept deprecated. The file is preserved for links "
                "and history; deprecated concepts are no longer injected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept_id": {
                        "type": "string",
                        "description": "Concept ID (relative path without .md)",
                    },
                },
                "required": ["concept_id"],
            },
            handler=handle_concept_deprecate,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="concept_set_context",
            description=(
                "Flip a concept between 'auto' (injected every turn, within "
                f"the {WORKING_CONCEPTS_TOKEN_BUDGET}-token budget) and "
                "'search-only' (retrievable via concept_search only)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept_id": {
                        "type": "string",
                        "description": "Concept ID (relative path without .md)",
                    },
                    "context": {
                        "type": "string",
                        "enum": list(CONTEXT_VALUES),
                    },
                },
                "required": ["concept_id", "context"],
            },
            handler=handle_concept_set_context,
        )
    )

    # 3. Slash Commands
    async def okf_command(args: str = "") -> str:
        parts = args.strip().split()
        subcmd = parts[0] if parts else "status"

        if subcmd == "status":
            trust = graph.trust_summary()
            stale = graph.stale_count()
            types_str = (
                ", ".join(f"{k}: {v}" for k, v in sorted(graph.types_summary().items()))
                or "None"
            )
            layers_str = (
                ", ".join(f"{graph.layer_label(p)}:{p}" for p in graph.bundle_layers)
                or "none"
            )
            return (
                "OKF Knowledge Bundle Status:\n"
                f"  Layers:          {layers_str}\n"
                f"  Directory:       {graph.bundle_root or 'MISSING: No bundle loaded'}\n"
                f"  Total Concepts:  {len(graph.concepts)}\n"
                f"  Types Breakdown: {types_str}\n"
                f"  Human-Reviewed:  {trust['human-reviewed']}\n"
                f"  Machine-Confirmed: {trust['machine-confirmed']}\n"
                f"  Unverified:      {trust['unverified']}\n"
                f"  Stale Concepts:  {stale}"
            )
        if subcmd == "search":
            query = " ".join(parts[1:])
            results = graph.search(query=query)
            if not results:
                return f"MISSING: No concepts found matching '{query}'."
            lines = [f"FOUND: {len(results)} concepts matching '{query}':"]
            for c in results[:10]:
                lines.append(
                    f"  - {c.id} ({c.type}) [{c.trust_tier.value}]: {c.title or c.description}"
                )
            return "\n".join(lines)
        if subcmd == "validate":
            merged = _merged_validation_report(graph)
            status_text = (
                "OK: Bundle is conformant."
                if merged["valid"]
                else "FAIL: Bundle is non-conformant."
            )
            return (
                f"{status_text}\n"
                f"  Concepts: {merged['concepts_checked']}, "
                f"Errors: {len(merged['errors'])}, "
                f"Warnings: {len(merged['warnings'])}"
            )
        if subcmd == "graph":
            html = generate_html_graph(graph)
            out_file = (graph.bundle_root or Path(".")) / "viz.html"
            out_file.write_text(html, encoding="utf-8")
            return f"OK: Interactive graph rendered to {out_file}."

        return f"Unknown okf command: '{subcmd}'. Usage: /okf [status|search <query>|validate|graph]"

    api.register_command(
        "okf",
        description="Manage and query Open Knowledge Format bundle",
        handler=okf_command,
    )
