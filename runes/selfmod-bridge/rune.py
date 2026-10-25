"""selfmod-bridge rune — load-time entry point.

The engine loads ``manifest.entry_point`` (``rune.py``) and calls the
module-level ``rune_factory(api)``. The factory is constructed inside the
package and re-exported here, matching the skills-bridge convention.
"""

from mvge_runes_selfmod_bridge.rune import (
    SelfmodBridgeRune,
    create_rune,
    rune_factory,
)

__all__ = ["SelfmodBridgeRune", "create_rune", "rune_factory"]
