"""Canonical scaffold generators (normative, spec §5.5).

``templates.py`` is the single generator for all scaffolded content.

Safe-quoting is normative: model-controlled strings are NEVER interpolated
into generated source via string formatting. Python source embeds them as
``json.dumps()`` string literals (valid docstrings that round-trip — a
leading ``"..."`` literal is the module/function docstring and
``json.dumps`` cannot break out of it); JSON is built with the ``json``
module; markdown is inert but gets the same treatment where trivial.
``ensure_ascii=False`` throughout: the default ASCII escaping turns
non-BMP characters into lone-surrogate backslash-u escapes, which do NOT
round-trip through Python string literals or ``yaml.safe_load``.
"""

from __future__ import annotations

import json


def spell_source(name: str, description: str) -> str:
    """Generate ``<name>.py`` — exactly one public function, named == file stem.

    Discovery rule (``function_spell.py``): a stem-named callable
    short-circuits; extra public helpers are *silently ignored*, not an
    error — which is worse for the author. Hence helpers MUST be
    ``_private``, and the template's docstring says so.
    """
    doc = json.dumps(description, ensure_ascii=False)
    return (
        f"{doc}\n"
        "\n"
        '"""Read the AGENTS.md in this directory for exact syntax, rules, and contracts\n'
        "before creating or modifying spells.\n"
        f"(Discovery ignores any public function that isn't named {name} — "
        "keep helpers _private.)\n"
        '"""\n'
        "\n"
        f"def {name}(text: str) -> str:\n"
        f"    {doc}\n"
        f'    raise NotImplementedError("Implement {name}: replace this body.")\n'
    )


def _rune_class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Rune"


def rune_tree(name: str, description: str) -> dict[str, str]:
    """Generate the full scaffolded-rune file tree as relpath -> content.

    Mirrors the verified steering-bridge packaging contract. NEVER writes
    ``.install-id`` — owned by the ``mvge rune install`` flow.
    """
    pkg = f"mvge_runes_{name}"
    cls = _rune_class_name(name)
    doc = json.dumps(description, ensure_ascii=False)

    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": description,
        "types": ["Sigil"],
        "runtime": "python",
        "entry_point": "rune.py",
        "hooks": ["session_start"],
        "execution_mode": "parallel",
        "enabled": True,
    }

    pyproject = (
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        f"description = {doc}\n"
        'readme = "README.md"\n'
        'requires-python = ">=3.13"\n'
        "dependencies = [\n"
        '    "typer>=0.12",\n'
        "]\n"
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        "include = [\n"
        f'    "{pkg}/",\n'
        '    "rune.py",\n'
        '    "cli.py",\n'
        "]\n"
        "\n"
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["."]\n'
        'testpaths = ["tests"]\n'
        'python_files = ["*.py"]\n'
        'addopts = "--import-mode=importlib"\n'
    )

    root_rune = (
        f"{doc}\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        f"from {pkg}.rune import {cls}, rune_factory\n"
        "\n"
        f'__all__ = ["{cls}", "rune_factory"]\n'
    )

    package_rune = (
        f"{doc}\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import Any\n"
        "\n"
        "\n"
        f"class {cls}:\n"
        f'    """{cls} rune skeleton."""\n'
        "\n"
        "    def __init__(self, api: Any | None = None) -> None:\n"
        "        self._api = api\n"
        "\n"
        "    async def on_session_start(self, data: Any = None) -> None:\n"
        '        """Session-start hook skeleton."""\n'
        "\n"
        "\n"
        f"def rune_factory(api: Any) -> {cls}:\n"
        '    """Instantiate the rune."""\n'
        f"    return {cls}(api)\n"
    )

    package_init = f"{doc}\n"

    cli = (
        f"{doc}\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import typer\n"
        "\n"
        f"cli_app = typer.Typer(name={doc})\n"
        "\n"
        "\n"
        "@cli_app.command()\n"
        "def main() -> None:\n"
        f'    """{name} CLI entry point."""\n'
        "    raise NotImplementedError\n"
    )

    readme = f"# {name}\n\n{description}\n"

    skeleton_test = (
        f"{doc}\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def test_skeleton() -> None:\n"
        "    assert True\n"
    )

    return {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "pyproject.toml": pyproject,
        "rune.py": root_rune,
        "cli.py": cli,
        "README.md": readme,
        f"{pkg}/__init__.py": package_init,
        f"{pkg}/rune.py": package_rune,
        "tests/test_skeleton.py": skeleton_test,
    }


def skill_markdown(name: str, description: str) -> str:
    """Generate ``<name>/SKILL.md`` — frontmatter + body.

    Frontmatter schema is pinned to the skills-bridge loader's parser:
    ``name`` + ``description`` required (the loader strictly skips
    missing/empty descriptions). ``description`` is embedded as a
    ``json.dumps`` double-quoted YAML scalar — valid YAML, round-trips
    through ``yaml.safe_load`` and the loader's ``raw_desc.strip()``.
    """
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{description}\n"
        "\n"
        "## Usage\n"
        "\n"
        "Describe how to use this skill here.\n"
    )


def spells_agents_md() -> str:
    """Normative spells-dir ``AGENTS.md`` seed text (spec §5.4, verbatim)."""
    return (
        "# Spells\n"
        "\n"
        "FunctionSpells: one `<name>.py` per spell, exactly one public function named\n"
        "== the file stem. All helpers MUST be `_private` — discovery silently ignores\n"
        "any other public function. Read this file before creating or modifying spells.\n"
    )
