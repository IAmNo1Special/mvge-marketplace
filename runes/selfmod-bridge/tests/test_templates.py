"""Golden + adversarial tests for the normative scaffold templates.

Safe-quoting is normative (spec §5.5): model-controlled strings are NEVER
interpolated into generated source via string formatting. Adversarial
inputs must round-trip through ``ast.get_docstring``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from mvge.agent.function_spell import discover_spells_from_dir
from selfmod_bridge_conftest import read_skill_frontmatter

from mvgeos_runes_selfmod_bridge.templates import (
    rune_tree,
    skill_markdown,
    spell_source,
    spells_agents_md,
)

ADVERSARIAL = (
    'Say """hi""" to ${name}\n'
    "line two with `backticks` and 'quotes'\n"
    "unicode: \u2603 \U0001f680 trailing spaces   \n"
    "ends with backslash \\"
)


def test_spell_source_golden() -> None:
    src = spell_source("hello", "Say hello.")
    assert src == (
        '"Say hello."\n'
        "\n"
        '"""Read the AGENTS.md in this directory for exact syntax, rules, and contracts\n'
        "before creating or modifying spells.\n"
        "(Discovery ignores any public function that isn't named hello — "
        "keep helpers _private.)\n"
        '"""\n'
        "\n"
        "def hello(text: str) -> str:\n"
        '    "Say hello."\n'
        '    raise NotImplementedError("Implement hello: replace this body.")\n'
    )


@pytest.mark.parametrize("description", [ADVERSARIAL, "", "plain", '"""', "\n\n\n"])
def test_spell_source_adversarial_roundtrip(description: str) -> None:
    src = spell_source("hello", description)
    tree = ast.parse(src)  # must be valid Python no matter the input
    assert ast.get_docstring(tree, clean=False) == description


def test_spell_source_exactly_one_public_function() -> None:
    src = spell_source("hello", "Say hello.")
    tree = ast.parse(src)
    public = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not n.name.startswith("_")
    ]
    assert public == ["hello"]


def test_scaffolded_spell_passes_real_discovery(tmp_path: Path) -> None:
    """The real engine discovery must load the scaffolded spell by stem rule."""

    (tmp_path / "hello.py").write_text(spell_source("hello", "Say hello."))
    spells = discover_spells_from_dir(tmp_path)
    assert len(spells) == 1
    spell = spells[0]
    name = getattr(spell, "name", None) or getattr(getattr(spell, "func", None), "__name__", None)
    assert name == "hello"


def test_rune_tree_golden_manifest() -> None:
    tree = rune_tree("myrune", "Does things.")
    manifest = json.loads(tree["manifest.json"])
    assert manifest["name"] == "myrune"
    assert manifest["version"] == "0.1.0"
    assert manifest["description"] == "Does things."
    assert manifest["runtime"] == "python"
    assert manifest["entry_point"] == "rune.py"
    assert manifest["enabled"] is True
    assert manifest["execution_mode"] == "parallel"
    assert isinstance(manifest["types"], list) and manifest["types"]
    assert all(h == h.lower() for h in manifest["hooks"])  # snake_case hooks


def test_rune_tree_required_files() -> None:
    tree = rune_tree("myrune", "Does things.")
    pkg = "mvgeos_runes_myrune"
    assert set(tree) == {
        "manifest.json",
        "pyproject.toml",
        "rune.py",
        "cli.py",
        "README.md",
        f"{pkg}/__init__.py",
        f"{pkg}/rune.py",
        "tests/test_skeleton.py",
    }
    assert ".install-id" not in tree  # never written — owned by `mvgeos rune install`


def test_rune_tree_pyproject_contract() -> None:
    tree = rune_tree("myrune", "Does things.")
    text = tree["pyproject.toml"]
    assert 'requires-python = ">=3.13"' in text
    assert "hatchling" in text
    assert "mvgeos_runes_myrune/" in text
    assert '"rune.py"' in text and '"cli.py"' in text
    assert 'python_files = ["*.py"]' in text
    assert "importlib" in text


def test_rune_tree_root_rune_reexports_factory(tmp_path: Path) -> None:
    """Materialize the tree and import the root rune.py for real."""

    tree = rune_tree("myrune", "Does things.")
    for rel, content in tree.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        spec = importlib.util.spec_from_file_location("scaffolded_root_rune", tmp_path / "rune.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(tmp_path))
    assert callable(getattr(module, "rune_factory", None))
    # The package skeleton imports cleanly too.
    pkg_spec = importlib.util.spec_from_file_location(
        "mvgeos_runes_myrune.rune", tmp_path / "mvgeos_runes_myrune" / "rune.py"
    )
    assert pkg_spec is not None and pkg_spec.loader is not None
    pkg_module = importlib.util.module_from_spec(pkg_spec)
    sys.path.insert(0, str(tmp_path))
    try:
        pkg_spec.loader.exec_module(pkg_module)
    finally:
        sys.path.remove(str(tmp_path))
    assert callable(getattr(pkg_module, "rune_factory", None))


@pytest.mark.parametrize("description", [ADVERSARIAL, '"""'])
def test_rune_tree_adversarial_roundtrip(tmp_path: Path, description: str) -> None:
    tree = rune_tree("myrune", description)
    for rel, content in tree.items():
        if not rel.endswith(".py"):
            continue
        parsed = ast.parse(content)  # valid Python regardless of input
        # Spec §5.5: EVERY generated .py file embeds the description as its
        # module docstring — no carve-outs, not even the re-export shim.
        assert ast.get_docstring(parsed, clean=False) == description, rel
    # JSON built with the json module — description round-trips exactly.
    assert json.loads(tree["manifest.json"])["description"] == description


def test_skill_markdown_frontmatter_schema(tmp_path: Path) -> None:
    """The generated frontmatter satisfies the published schema on its own terms."""

    text = skill_markdown("myskill", "  Does skill things.  ")
    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    manifest = read_skill_frontmatter(skill_dir)
    assert manifest.name == "myskill"
    # Loaders compare the STRIPPED description -- assert against that form.
    assert manifest.description == "Does skill things."
    assert manifest.body.startswith("# myskill")


@pytest.mark.parametrize("description", [ADVERSARIAL, '"""', "a: b # c"])
def test_skill_markdown_adversarial(tmp_path: Path, description: str) -> None:
    """Adversarial descriptions survive YAML quoting, not just Python escaping.

    The description is embedded as a ``json.dumps`` double-quoted scalar, and
    JSON and YAML double-quoted scalars agree on the escape sequences this can
    produce -- so it round-trips through ``yaml.safe_load`` back to the exact
    input, trailing backslash and all. Asserting that here is stronger than
    asserting a sibling loader accepted it: a lenient loader that repairs or
    skips would have masked the defect.
    """

    text = skill_markdown("myskill", description)
    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    manifest = read_skill_frontmatter(skill_dir)
    assert manifest.description == description.strip()


def test_spells_agents_md_seed_verbatim() -> None:
    assert spells_agents_md() == (
        "# Spells\n"
        "\n"
        "FunctionSpells: one `<name>.py` per spell, exactly one public function named\n"
        "== the file stem. All helpers MUST be `_private` — discovery silently ignores\n"
        "any other public function. Read this file before creating or modifying spells.\n"
    )
