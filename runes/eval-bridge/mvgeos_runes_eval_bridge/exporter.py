from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal


def export_tome_to_records(
    tome_path: Path,
    export_format: Literal["sharegpt", "atropos"] = "sharegpt",
) -> dict[str, Any]:
    lines = tome_path.read_text(encoding="utf-8").splitlines()
    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    tome_id = tome_path.stem

    if export_format == "sharegpt":
        conversations: list[dict[str, str]] = []
        for entry in entries:
            role = entry.get("role", "")
            content = entry.get("content", "")
            text = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in (
                        "text",
                        "thinking",
                    ):
                        text += block.get("text", "")
            elif isinstance(content, str):
                text = content

            if role == "user":
                conversations.append({"from": "human", "value": text})
            elif role == "assistant":
                conversations.append({"from": "gpt", "value": text})
            elif role == "spellResult":
                conversations.append(
                    {"from": "system", "value": f"Tool output: {text}"}
                )

        return {"id": tome_id, "conversations": conversations}

    # Atropos format
    turns: list[dict[str, Any]] = []
    for entry in entries:
        role = entry.get("role", "")
        content = entry.get("content", "")
        text = ""
        tool_calls: list[dict[str, Any]] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text += block.get("text", "")
                    elif block.get("type") == "spell_cast":
                        tool_calls.append(block)
        elif isinstance(content, str):
            text = content

        turn_data: dict[str, Any] = {"role": role, "text": text}
        if tool_calls:
            turn_data["tool_calls"] = tool_calls
        turns.append(turn_data)

    return {"session_id": tome_id, "turns": turns}


def export_rl_data(
    tome_path: Path,
    output_path: Path | None = None,
    export_format: Literal["sharegpt", "atropos"] = "sharegpt",
) -> str:
    record = export_tome_to_records(tome_path, export_format)
    rendered = json.dumps(record, indent=2)
    if output_path is not None:
        output_path.write_text(rendered, encoding="utf-8")
    return rendered
