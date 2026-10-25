"""Rune factory and lifecycle hook registration for adr-bridge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import BeforeMvgeStartData, SigilHook, SpellDefinition

from mvgeos_runes_adr_bridge.parser import load_adrs
from mvgeos_runes_adr_bridge.prompt import (
    render_adr_catalog,
    update_invocations_with_adr_catalog,
)
from mvgeos_runes_adr_bridge.scaffold import scaffold_new_adr
from mvgeos_runes_adr_bridge.sync import sync_adr_index
from mvgeos_runes_adr_bridge.validator import validate_adrs


def rune_factory(api: RuneAPI) -> None:
    """Initialize and bind ADR bridge rune to session lifecycle."""
    runner = getattr(api, "_runner", None)
    ctx = getattr(runner, "context", None) if runner else None
    raw_cwd = getattr(ctx, "cwd", None) if ctx else None
    cwd: Path | None = Path(raw_cwd) if raw_cwd else None

    # In-memory ADR collection
    adrs = load_adrs(cwd=cwd)
    catalog_xml = render_adr_catalog(adrs)

    # 1. Lifecycle Hooks
    async def on_session_start(_data: Any = None) -> None:
        nonlocal adrs, catalog_xml
        adrs = load_adrs(cwd=cwd)
        catalog_xml = render_adr_catalog(adrs)

    async def on_before_mvge_start(data: Any = None) -> Any:
        nonlocal catalog_xml
        if not catalog_xml:
            return data

        if isinstance(data, BeforeMvgeStartData):
            data.base_prompt = f"{data.base_prompt}\n\n{catalog_xml}".strip()
            return data
        if isinstance(data, dict) and "base_prompt" in data:
            data["base_prompt"] = f"{data['base_prompt']}\n\n{catalog_xml}".strip()
            return data
        return data

    async def on_context_transform(invocations: Any = None) -> Any:
        if isinstance(invocations, list):
            return update_invocations_with_adr_catalog(invocations, catalog_xml)
        return invocations

    async def on_session_shutdown(_data: Any = None) -> None:
        nonlocal adrs, catalog_xml
        adrs = []
        catalog_xml = ""

    api.on(SigilHook.SESSION_START, on_session_start)
    api.on(SigilHook.BEFORE_MVGE_START, on_before_mvge_start)
    api.on(SigilHook.CONTEXT_TRANSFORM, on_context_transform)
    api.on(SigilHook.SESSION_SHUTDOWN, on_session_shutdown)

    # 2. Spells
    async def handle_adr_list(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        status_filter = (
            str(payload.get("status", "")).strip().lower()
            if payload.get("status")
            else None
        )

        items = [
            a
            for a in adrs
            if status_filter is None or a.status.lower() == status_filter
        ]
        data = [
            {
                "number": a.number,
                "title": a.title,
                "status": a.status,
                "date": a.date,
                "filename": a.path.name,
            }
            for a in items
        ]
        return json.dumps(data, indent=2)

    async def handle_adr_get(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        num = int(payload.get("number", 0))
        target_adr = next((a for a in adrs if a.number == num), None)
        if target_adr is None:
            return json.dumps({"error": f"ADR '{num:04d}' not found."})

        data = {
            "number": target_adr.number,
            "title": target_adr.title,
            "status": target_adr.status,
            "date": target_adr.date,
            "deciders": target_adr.deciders,
            "context_and_problem_statement": target_adr.context_and_problem_statement,
            "decision_outcome": target_adr.decision_outcome,
            "considered_options": target_adr.considered_options,
            "consequences": target_adr.consequences,
            "filename": target_adr.path.name,
        }
        return json.dumps(data, indent=2)

    async def handle_adr_validate(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = params if params is not None else arguments or {}
        adr_dir_arg = payload.get("adr_dir")
        target_path = Path(str(adr_dir_arg)) if adr_dir_arg else None
        rep = validate_adrs(cwd=cwd, adr_dir=target_path)
        data = {
            "valid": rep.valid,
            "errors": [
                {"filename": e.filename, "message": e.message} for e in rep.errors
            ],
            "warnings": [
                {"filename": w.filename, "message": w.message} for w in rep.warnings
            ],
            "records_checked": len(rep.adrs),
        }
        return json.dumps(data, indent=2)

    async def handle_adr_new(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        nonlocal adrs, catalog_xml
        payload = params if params is not None else arguments or {}
        title = str(payload.get("title", "")).strip()
        context = str(payload.get("context", "")).strip()
        status = str(payload.get("status", "proposed")).strip()
        deciders = str(payload.get("deciders", "")).strip()

        if not title:
            return json.dumps({"error": "ADR title is required."})

        created = scaffold_new_adr(
            title=title,
            cwd=cwd,
            context=context,
            status=status,
            deciders=deciders,
        )

        # Refresh in-memory list
        adrs = load_adrs(cwd=cwd)
        catalog_xml = render_adr_catalog(adrs)

        return json.dumps(
            {
                "status": "created",
                "filename": created.name,
                "path": str(created),
            },
            indent=2,
        )

    api.register_spell(
        SpellDefinition(
            name="adr_list",
            description="List Architectural Decision Records (MADR 3.0) in the project.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional filter by status (e.g. accepted, proposed)",
                    },
                },
            },
            handler=handle_adr_list,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="adr_get",
            description="Retrieve detailed decision outcome, options, and consequences for an ADR by number.",
            parameters={
                "type": "object",
                "properties": {
                    "number": {
                        "type": "integer",
                        "description": "ADR sequential number (e.g. 1 for 0001)",
                    },
                },
                "required": ["number"],
            },
            handler=handle_adr_get,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="adr_validate",
            description="Validate Architectural Decision Records against MADR 3.0 schema and index sync.",
            parameters={
                "type": "object",
                "properties": {
                    "adr_dir": {
                        "type": "string",
                        "description": "Optional custom ADR directory path",
                    },
                },
            },
            handler=handle_adr_validate,
        )
    )

    api.register_spell(
        SpellDefinition(
            name="adr_new",
            description="Scaffold a new MADR 3.0 Architectural Decision Record and update docs/adr/README.md.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the decision record",
                    },
                    "context": {
                        "type": "string",
                        "description": "Context and problem statement",
                    },
                    "status": {
                        "type": "string",
                        "description": "Initial status (default: proposed)",
                    },
                    "deciders": {
                        "type": "string",
                        "description": "Deciders string (e.g. team or individual)",
                    },
                },
                "required": ["title"],
            },
            handler=handle_adr_new,
        )
    )

    # 3. Slash Command
    async def adr_command(args: str = "") -> str:
        nonlocal adrs, catalog_xml
        parts = args.strip().split()
        subcmd = parts[0] if parts else "list"

        if subcmd == "list":
            if not adrs:
                return "MISSING: No ADRs found in docs/adr."
            lines = [f"FOUND: {len(adrs)} Architectural Decision Records:"]
            for a in adrs:
                lines.append(f"  ADR {a.number:04d}: {a.title} [{a.status}]")
            return "\n".join(lines)

        if subcmd == "get":
            if len(parts) < 2:
                return "FAIL: Missing ADR number. Usage: /adr get <number>"
            try:
                num = int(parts[1])
            except ValueError:
                return f"FAIL: Invalid ADR number '{parts[1]}'."
            target_adr = next((a for a in adrs if a.number == num), None)
            if not target_adr:
                return f"MISSING: ADR {num:04d} not found."
            return (
                f"ADR {target_adr.number:04d}: {target_adr.title}\n"
                f"  Status:   {target_adr.status}\n"
                f"  Date:     {target_adr.date or 'None'}\n"
                f"  Outcome:  {target_adr.decision_outcome[:200]}..."
            )

        if subcmd == "validate":
            rep = validate_adrs(cwd=cwd)
            if rep.valid:
                return f"OK: All {len(rep.adrs)} ADRs conform to MADR 3.0."
            return f"FAIL: Found {len(rep.errors)} errors in ADRs."

        if subcmd == "sync":
            msg = sync_adr_index(cwd=cwd)
            adrs = load_adrs(cwd=cwd)
            catalog_xml = render_adr_catalog(adrs)
            return msg

        if subcmd == "new":
            title = " ".join(parts[1:]).strip()
            if not title:
                return "FAIL: Missing title. Usage: /adr new <Title>"
            created = scaffold_new_adr(title=title, cwd=cwd)
            adrs = load_adrs(cwd=cwd)
            catalog_xml = render_adr_catalog(adrs)
            return f"OK: Scaffolding created at {created.name}."

        return f"Unknown adr command: '{subcmd}'. Usage: /adr [list|get <num>|validate|sync|new <title>]"

    api.register_command(
        "adr",
        description="Manage and query Architectural Decision Records (MADR 3.0)",
        handler=adr_command,
    )
