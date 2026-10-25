from __future__ import annotations

from pathlib import Path

from mvge_runes_steering_bridge.prompt import (
    build_project_context,
    build_steering_pointers,
    build_steering_section,
    discover_subpackage_pointers,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_build_project_context_workspace_and_global(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    _write(
        global_dir / "AGENTS.md",
        "---\nkind: global\n---\nGlobal instructions here",
    )

    cwd_dir = tmp_path / "workspace"
    _write(
        cwd_dir / ".agents" / "AGENTS.md",
        "# Project Guidelines\nWorkspace instructions here",
    )

    from mvge_runes_steering_bridge.resolver import (
        resolve_global_agents_file,
        resolve_workspace_agents_file,
    )

    ws_ref = resolve_workspace_agents_file(cwd_dir)
    gl_ref = resolve_global_agents_file(global_dir)
    assert ws_ref is not None
    assert gl_ref is not None

    block = build_project_context(ws_ref, gl_ref)

    assert "<project_context>" in block
    assert "</project_context>" in block
    assert '<global_instructions path="' in block
    assert "Global instructions here" in block
    assert '<project_instructions path=".agents/AGENTS.md">' in block
    assert "Workspace instructions here" in block
    # YAML frontmatter must be stripped
    assert "kind: global" not in block


def test_build_project_context_empty_refs() -> None:
    assert build_project_context(None, None) == ""


def test_build_project_context_ignores_empty_files(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    _write(global_dir / "AGENTS.md", "   \n  \t ")

    cwd_dir = tmp_path / "workspace"
    cwd_dir.mkdir()
    _write(cwd_dir / "AGENTS.md", "---\nkind: empty\n---\n   ")

    from mvge_runes_steering_bridge.resolver import (
        resolve_global_agents_file,
        resolve_workspace_agents_file,
    )

    assert (
        build_project_context(
            resolve_workspace_agents_file(cwd_dir),
            resolve_global_agents_file(global_dir),
        )
        == ""
    )


def test_discover_subpackage_pointers(tmp_path: Path) -> None:
    cwd_dir = tmp_path / "repo"
    cwd_dir.mkdir()
    _write(cwd_dir / "AGENTS.md", "Root rules")

    _write(cwd_dir / "mvge-core" / "AGENTS.md", "Core secret internals")
    _write(cwd_dir / "mvge-gui" / "agents.md", "GUI secret internals")
    (cwd_dir / "regular_folder").mkdir()
    (cwd_dir / ".hidden_pkg" / "AGENTS.md").parent.mkdir(parents=True)
    _write(cwd_dir / ".hidden_pkg" / "AGENTS.md", "Hidden rules")
    (cwd_dir / "__pycache__").mkdir(exist_ok=True)

    pointers = discover_subpackage_pointers(cwd_dir)

    assert ("mvge-core", "mvge-core/AGENTS.md") in pointers
    assert ("mvge-gui", "mvge-gui/agents.md") in pointers
    assert all(name != "regular_folder" for name, _ in pointers)
    assert all(not name.startswith(".") for name, _ in pointers)
    assert all(name != "__pycache__" for name, _ in pointers)


def test_build_steering_pointers_format(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "Root rules")
    _write(tmp_path / "pkg" / "AGENTS.md", "Pkg rules")
    _write(tmp_path / "g" / "AGENTS.md", "Global rules")

    from mvge_runes_steering_bridge.resolver import (
        resolve_global_agents_file,
        resolve_workspace_agents_file,
    )

    ws_ref = resolve_workspace_agents_file(tmp_path)
    gl_ref = resolve_global_agents_file(tmp_path / "g")
    lines = build_steering_pointers(ws_ref, gl_ref, [("pkg", "pkg/AGENTS.md")])

    assert "- Global Rules: " in "\n".join(lines)
    assert "- Project Rules: AGENTS.md" in lines
    assert "- Subpackage Rules (pkg): pkg/AGENTS.md" in lines


def test_build_steering_section_inlines_root_not_subpackages(
    tmp_path: Path,
) -> None:
    from mvge_runes_steering_bridge.resolver import (
        resolve_workspace_agents_file,
    )

    _write(tmp_path / "AGENTS.md", "Root rules")
    _write(tmp_path / "mvge-core" / "AGENTS.md", "Core secret internals")

    ws_ref = resolve_workspace_agents_file(tmp_path)
    subs = discover_subpackage_pointers(tmp_path)
    section = build_steering_section(ws_ref, None, subs)

    # Root instructions must be inlined
    assert '<project_instructions path="AGENTS.md">' in section
    assert "Root rules" in section
    # Subpackages must be listed as on-demand pointers only
    assert "- Subpackage Rules (mvge-core): mvge-core/AGENTS.md" in section
    # Subpackage internals must NOT be inlined (token budget protection)
    assert "Core secret internals" not in section


def test_build_steering_section_empty() -> None:
    assert build_steering_section(None, None, []) == ""
    assert build_steering_section() == ""
