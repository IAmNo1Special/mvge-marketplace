"""selfmod-bridge: official self-modification & customization bridge for Mvge."""

from __future__ import annotations

from mvge_runes_selfmod_bridge.prompt import build_selfmod_section
from mvge_runes_selfmod_bridge.rune import SelfmodBridgeRune, create_rune, rune_factory
from mvge_runes_selfmod_bridge.skillspec import skill_markdown
from mvge_runes_selfmod_bridge.spells import SelfmodSpellsMixin
from mvge_runes_selfmod_bridge.state import SelfmodState
from mvge_runes_selfmod_bridge.status import describe_extensions, list_snapshots
from mvge_runes_selfmod_bridge.templates import (
    rune_tree,
    spell_source,
    spells_agents_md,
)
from mvge_runes_selfmod_bridge.validation import (
    ValidationError,
    validate_extension_name,
    validate_label,
    validate_scope,
    validate_target_dir,
)

__all__: list[str] = [
    "SelfmodBridgeRune",
    "SelfmodSpellsMixin",
    "SelfmodState",
    "ValidationError",
    "build_selfmod_section",
    "create_rune",
    "describe_extensions",
    "list_snapshots",
    "rune_factory",
    "rune_tree",
    "skill_markdown",
    "spell_source",
    "spells_agents_md",
    "validate_extension_name",
    "validate_label",
    "validate_scope",
    "validate_target_dir",
]
