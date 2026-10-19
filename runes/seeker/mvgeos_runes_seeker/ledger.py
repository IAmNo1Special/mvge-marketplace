from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SeekerLedger:
    """Discovery and activation ledger for a Seeker session."""

    discovered_spells: set[str] = field(default_factory=set)
    discovered_skills: set[str] = field(default_factory=set)
    discovered_mcps: set[str] = field(default_factory=set)
    activated_skills: set[str] = field(default_factory=set)
    last_active_turn: dict[str, int] = field(default_factory=dict)
    current_turn: int = 0
