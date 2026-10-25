"""CLI surface for selfmod-bridge: ``/selfmod``.

This is a *debug surface* — the same visibility the engine already
gives operators; the spells are the supported surface. It does not add
new power, so no approval interplay applies here.
"""

from __future__ import annotations

from mvge_runes_selfmod_bridge.rune import SelfmodBridgeRune


class SelfmodCLI:
    """``/selfmod`` command handler, delegating to the rune."""

    def __init__(self, rune: SelfmodBridgeRune) -> None:
        self.rune = rune

    async def handle(self, args_str: str = "") -> str:
        """Dispatch ``/selfmod [status|show]`` to the rune."""
        return await self.rune.handle_selfmod_command(args_str)
