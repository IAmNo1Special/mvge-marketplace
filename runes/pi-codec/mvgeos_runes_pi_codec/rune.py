"""Rune factory for pi-codec.

pi-codec is a CLI-only rune: the commands are invoked by the user as
`mvgeos pi-export <tome>` and `mvgeos pi-validate <file>`, never by the
agent mid-session, so there is no session wiring here. The loader requires
a `rune_factory`; it is a no-op.
"""

from __future__ import annotations

from mvge.runes.rune_api import RuneAPI


def rune_factory(api: RuneAPI) -> None:
    """Bind pi-codec. Nothing to wire: the CLI command carries the feature."""
    _ = api
