"""SKILL.md template surface.

The canonical generator lives in ``templates.skill_markdown`` (spec §5.5:
``templates.py`` is the single generator — no duplication). This module
documents the frontmatter schema contract and re-exports the generator.

Frontmatter schema (pinned to the skills-bridge loader's parser,
``skills-bridge/.../parser.py::parse_skill_manifest``):

- ``name`` (required): must match ``^[a-z0-9]+(-[a-z0-9]+)*$`` and the
  skill directory name (lenient warnings otherwise).
- ``description`` (required): the loader STRICTLY skips the skill when it
  is missing or empty — ``scaffold_skill`` therefore requires a
  non-empty description. The parser applies ``raw_desc.strip()`` before
  comparing, so golden tests assert against the stripped input.
- Optional: ``version``, ``license``, ``compatibility``, ``metadata``
  (mapping), ``allowed-tools`` / ``allowed_tools``, and
  ``disable-model-invocation``.

The description is embedded as a ``json.dumps(ensure_ascii=False)``
double-quoted YAML scalar — valid YAML that round-trips through
``yaml.safe_load`` back to the exact (stripped) input.
"""

from __future__ import annotations

from mvge_runes_selfmod_bridge.templates import skill_markdown

__all__ = ["skill_markdown"]
