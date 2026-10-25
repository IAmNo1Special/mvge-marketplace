"""Rune entry point for opencode-realm.

The engine resolves ``manifest.entry_point`` relative to the artifact directory,
so this file has to stay at the Rune's root even though the implementation lives
in the package beside it.
"""

from __future__ import annotations

from mvge_runes_opencode_realm.rune import rune_factory

__all__ = ["rune_factory"]
