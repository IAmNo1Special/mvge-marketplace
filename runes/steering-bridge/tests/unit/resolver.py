from __future__ import annotations

from pathlib import Path

import pytest
from mvge_runes_steering_bridge.resolver import (
    read_steering_text,
    resolve_global_agents_file,
    resolve_scoped_agents_file,
    resolve_workspace_agents_file,
)


def test_resolve_workspace_agents_file_precedence(tmp_path: Path) -> None:
    # 1. Root AGENTS.md takes precedence over .agents/AGENTS.md
    ws1 = tmp_path / "ws1"
    (ws1 / ".agents").mkdir(parents=True)
    (ws1 / "AGENTS.md").write_text("root upper", encoding="utf-8")
    (ws1 / ".agents" / "AGENTS.md").write_text("dot upper", encoding="utf-8")
    res1 = resolve_workspace_agents_file(ws1)
    assert res1 is not None
    assert res1[0] == ws1 / "AGENTS.md"
    assert res1[1] == "AGENTS.md"

    # 2. Lowercase root agents.md works
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / "agents.md").write_text("root lower", encoding="utf-8")
    res2 = resolve_workspace_agents_file(ws2)
    assert res2 is not None
    assert res2[0] == ws2 / "agents.md"
    assert res2[1] == "agents.md"

    # 3. .agents/AGENTS.md works when root is absent
    ws3 = tmp_path / "ws3"
    (ws3 / ".agents").mkdir(parents=True)
    (ws3 / ".agents" / "AGENTS.md").write_text("dot upper", encoding="utf-8")
    res3 = resolve_workspace_agents_file(ws3)
    assert res3 is not None
    assert res3[0] == ws3 / ".agents" / "AGENTS.md"
    assert res3[1] == ".agents/AGENTS.md"

    # 4. .agents/agents.md works when root and upper are absent
    ws4 = tmp_path / "ws4"
    (ws4 / ".agents").mkdir(parents=True)
    (ws4 / ".agents" / "agents.md").write_text("dot lower", encoding="utf-8")
    res4 = resolve_workspace_agents_file(ws4)
    assert res4 is not None
    assert res4[0] == ws4 / ".agents" / "agents.md"
    assert res4[1] == ".agents/agents.md"

    # 5. None when empty directory
    ws5 = tmp_path / "ws5"
    ws5.mkdir()
    assert resolve_workspace_agents_file(ws5) is None


def test_resolve_workspace_agents_file_skips_empty(tmp_path: Path) -> None:
    ws = tmp_path / "ws_empty"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("   \n  \t ", encoding="utf-8")
    (ws / ".agents").mkdir()
    (ws / ".agents" / "AGENTS.md").write_text("dot content", encoding="utf-8")
    res = resolve_workspace_agents_file(ws)
    assert res is not None
    assert res[0] == ws / ".agents" / "AGENTS.md"


def test_resolve_global_agents_file(tmp_path: Path) -> None:
    global_missing = tmp_path / "missing"
    assert resolve_global_agents_file(global_missing) is None

    global_lower = tmp_path / "global_lower"
    global_lower.mkdir(parents=True)
    lower_file = global_lower / "agents.md"
    lower_file.write_text("global lower", encoding="utf-8")
    res_lower = resolve_global_agents_file(global_lower)
    assert res_lower is not None
    assert res_lower[0] == lower_file
    assert res_lower[1] == lower_file.as_posix()

    global_upper = tmp_path / "global_upper"
    global_upper.mkdir(parents=True)
    upper_file = global_upper / "AGENTS.md"
    upper_file.write_text("global upper", encoding="utf-8")
    res_upper = resolve_global_agents_file(global_upper)
    assert res_upper is not None
    assert res_upper[0] == upper_file
    assert res_upper[1] == upper_file.as_posix()


def test_resolve_global_agents_file_defaults_to_the_global_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no explicit directory, steering reads the relocated layer.

    Also pins the display string: the home-relative shorthand is only shown
    when the file really is in the home layer, because under a relocated
    global dir it would name a path that was never read.
    """
    global_dir = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
    global_dir.mkdir()
    steering_file = global_dir / "AGENTS.md"
    steering_file.write_text("relocated steering", encoding="utf-8")

    res = resolve_global_agents_file(None)

    assert res is not None
    assert res[0] == steering_file
    assert res[1] == steering_file.as_posix()


def test_resolve_scoped_agents_file(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "packages" / "core"
    nested_dir = pkg_dir / "src" / "deep"
    nested_dir.mkdir(parents=True)
    target_file = nested_dir / "mod.py"
    target_file.write_text("x = 1", encoding="utf-8")

    # No AGENTS.md anywhere
    assert resolve_scoped_agents_file(target_file, cwd=tmp_path) is None

    # Add AGENTS.md in pkg_dir
    pkg_agents = pkg_dir / "AGENTS.md"
    pkg_agents.write_text("package rules", encoding="utf-8")

    res = resolve_scoped_agents_file(target_file, cwd=tmp_path)
    assert res is not None
    assert res[0] == pkg_agents
    assert res[1] == "packages/core/AGENTS.md"

    # Target at pkg_dir itself
    res_dir = resolve_scoped_agents_file(pkg_dir, cwd=tmp_path)
    assert res_dir is not None
    assert res_dir[0] == pkg_agents


def test_read_steering_text_strip_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "with_fm.md"
    f.write_text(
        "---\nkind: agents\nversion: 1.0\n---\n# Real Content\nBody text",
        encoding="utf-8",
    )
    assert read_steering_text(f, strip_frontmatter=True) == "# Real Content\nBody text"
    assert read_steering_text(f, strip_frontmatter=False).startswith("---")

    f_only_fm = tmp_path / "only_fm.md"
    f_only_fm.write_text("---\nkind: agents\n---", encoding="utf-8")
    assert read_steering_text(f_only_fm, strip_frontmatter=True) == ""

    f_no_fm = tmp_path / "no_fm.md"
    f_no_fm.write_text("Plain markdown", encoding="utf-8")
    assert read_steering_text(f_no_fm, strip_frontmatter=True) == "Plain markdown"


def test_read_steering_text_missing_file(tmp_path: Path) -> None:
    assert read_steering_text(tmp_path / "does-not-exist.md") == ""
