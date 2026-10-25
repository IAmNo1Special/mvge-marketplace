"""skill-evolution rune -- load-time entry point.

The engine loads ``manifest.entry_point`` (``rune.py``) and calls the
module-level ``rune_factory(api)``. The factory is constructed inside the
package and re-exported here, matching the skills-bridge convention.

The manifest lives here rather than inside the package because the engine
reads ``<rune-dir>/manifest.json`` when installing; a manifest nested in the
package makes the rune uninstallable.
"""

from mvge_runes_skill_evolution.rune_factory import rune_factory

__all__ = ["rune_factory"]
