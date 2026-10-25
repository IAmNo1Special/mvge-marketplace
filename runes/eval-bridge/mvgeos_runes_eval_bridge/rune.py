from __future__ import annotations

from typing import Any

from mvge.runes.rune_api import RuneAPI

from mvgeos_runes_eval_bridge.cli import app


def rune_factory(api: RuneAPI) -> Any:
    api.register_command(
        name="eval",
        description="Run evaluation sets and export RL data from session Tomes",
        handler=app,
    )
    return app
