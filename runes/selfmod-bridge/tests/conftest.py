"""Shared test doubles for the selfmod-bridge rune.

``read_skill_frontmatter`` is a local reimplementation of the SKILL.md frontmatter
contract, and it is deliberately local. The obvious assertion for "the file
``scaffold_skill`` writes is loadable" is to call the skills-bridge parser, and
this suite used to do exactly that -- which put a sibling Rune on this Rune's
import path. Two consequences, both bad:

- The standalone suite could not run. ``pytest runes/selfmod-bridge/tests``
  resolved rootdir to this Rune, whose ``pythonpath`` is ``["."]``, so the
  sibling import failed at collection with two errors.
- Making it pass meant adding ``runes/skills-bridge`` to this Rune's
  ``pythonpath``. That is the boundary violation this gate exists to prevent:
  it makes the standalone suite depend on a sibling's presence, so a Rune
  extracted to ``~/.agents/extensions/`` no longer carries a suite that runs.

So the contract is asserted here instead, from the published schema rather than
from a sibling implementation. ``skillspec`` documents the four conditions a
SKILL.md must satisfy, and those four conditions are what selfmod is on the hook
for producing. Whether a *particular* loader honours them is that loader's test,
in that loader's suite.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mvge_runes_selfmod_bridge.rune import SelfmodBridgeRune
from mvge_runes_selfmod_bridge.state import SelfmodState

# Unique alias: the bare ``tests.conftest`` / ``rune`` module names collide
# across marketplace runes in full-suite runs (every rune ships
# ``tests/conftest.py`` and a top-level ``rune.py``, and ``tests`` becomes a
# namespace package spanning them all). pytest always imports this conftest
# before the test modules in this directory, so the alias is in place when
# they do ``from selfmod_bridge_conftest import ...``.
sys.modules.setdefault("selfmod_bridge_conftest", sys.modules[__name__])


class FakeApi:
    """Minimal RuneAPI double: records hook/command/spell registration,
    allowlist widening, emitted events, and audit calls."""

    def __init__(self) -> None:
        self.hooks: dict[Any, Any] = {}
        self.commands: dict[str, Any] = {}
        self.spells: dict[str, Any] = {}
        self.widened: list[str] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.audit_records: list[dict[str, Any]] = []
        self.audit_error: BaseException | None = None

    def on(self, hook: Any, handler: Any) -> None:
        self.hooks[hook] = handler

    def register_command(self, name: str, description: str = "", handler: Any = None) -> None:
        # Mirrors the real RuneAPI.register_command(name, description="",
        # handler=None) signature exactly — a positional handler in the
        # description slot must not silently become the description.
        assert isinstance(description, str), (
            f"register_command({name!r}): description must be a string, "
            f"got {type(description).__name__} — pass the handler as the "
            "third positional arg"
        )
        self.commands[name] = (description, handler)

    def register_spell(self, spell: Any) -> None:
        self.spells[spell.name] = spell

    def widen_global_allowlist(self, names: list[str]) -> None:
        self.widened.extend(names)

    def emit_event(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    def audit(
        self,
        op: str,
        *,
        outcome: str,
        code: str,
        message: str,
        target: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_error is not None:
            raise self.audit_error
        self.audit_records.append(
            {
                "op": op,
                "outcome": outcome,
                "code": code,
                "message": message,
                "target": target,
                "extra": extra,
            }
        )


def make_state(tmp_path: Path) -> SelfmodState:
    config = tmp_path / "agent-config"
    config.mkdir(exist_ok=True)
    spells = config / "spells"
    spells.mkdir(exist_ok=True)
    system = config / "SYSTEM.md"
    system.write_text("# Test System\n\nYou are a test agent.\n", encoding="utf-8")
    runes_root = tmp_path / "runes"
    runes_root.mkdir(exist_ok=True)
    return SelfmodState(
        config_dir=config,
        runes_paths=[runes_root],
        system_path=system,
        spells_dir=spells,
        agent_name="test-agent",
        cwd=tmp_path,
    )


def make_rune(tmp_path: Path, *, with_state: bool = True) -> tuple[Any, FakeApi]:
    api = FakeApi()
    rune = SelfmodBridgeRune(api, {"version": "0.1.0"})
    if with_state:
        rune.state = make_state(tmp_path)
    return rune, api


#: The ``name`` frontmatter grammar, per the .agents protocol / skillspec.
SKILL_NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Frontmatter is delimited by ``---`` lines around the YAML mapping.
_FRONTMATTER_REGEX = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?$", re.DOTALL)


@dataclass(frozen=True)
class SkillFrontmatter:
    """The fields a SKILL.md must carry for a loader to accept the skill."""

    name: str
    description: str
    body: str


def read_skill_frontmatter(skill_dir: Path) -> SkillFrontmatter:
    """Read ``<skill_dir>/SKILL.md`` and assert the published frontmatter contract.

    Asserts rather than returns ``None`` on failure: a manifest this Rune
    generated is never going to be valid for some other reason, so every
    violation here is a defect in ``skill_markdown`` or ``scaffold_skill``, and
    the assertion message should say which one.
    """
    skill_md = skill_dir / "SKILL.md"
    assert skill_md.is_file(), f"scaffold did not write {skill_md}"

    text = skill_md.read_text(encoding="utf-8")
    match = _FRONTMATTER_REGEX.match(text)
    assert match is not None, f"{skill_md} has no '---' frontmatter delimiters"
    raw_yaml, body = match.group(1), (match.group(2) or "").strip()

    frontmatter = yaml.safe_load(raw_yaml)
    assert isinstance(frontmatter, dict), (
        f"{skill_md} frontmatter is not a YAML mapping: {type(frontmatter).__name__}"
    )

    name = frontmatter.get("name")
    assert isinstance(name, str) and name.strip(), f"{skill_md} frontmatter has no 'name'"
    name = name.strip()
    # A loader validates the name and warns when it disagrees with the
    # directory; selfmod derives the directory from the same value, so a
    # mismatch is a selfmod defect, never a tolerated warning.
    assert SKILL_NAME_REGEX.match(name), f"name {name!r} does not match {SKILL_NAME_REGEX.pattern}"
    assert name == skill_dir.name, (
        f"frontmatter name {name!r} does not match directory {skill_dir.name!r}"
    )

    raw_description = frontmatter.get("description")
    # Loaders strip before comparing, so the stripped form is the contract.
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    assert description, f"{skill_md} frontmatter has no non-empty 'description'"

    return SkillFrontmatter(name=name, description=description, body=body)


def load_root_module(name: str) -> Any:
    """Load ``runes/selfmod-bridge/<name>.py`` under a unique module name.

    A bare ``import rune`` collides with every other marketplace rune's
    top-level ``rune.py`` in full-suite runs; the unique name keeps this
    rune's entry point unambiguous.
    """
    path = Path(__file__).resolve().parent.parent / f"{name}.py"
    module_name = f"selfmod_bridge_root_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod
