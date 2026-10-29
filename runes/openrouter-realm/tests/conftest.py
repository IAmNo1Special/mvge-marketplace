"""Hermeticity for this suite when run standalone.

A suite run by bare path (``pytest runes/openrouter-realm/tests``) resolves
its rootdir to this rune's own ``pyproject.toml``, so the repository-root
conftest never loads -- and with it, neither does the NO_PROXY sanitizer.
Constructing an OpenRouter realm builds an httpx client, which raises
``InvalidURL`` on bracketed IPv6 entries (``[::1]``) that sandboxes
commonly export. Strip those tokens here, mirroring the root conftest,
so this suite passes under any invocation.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


def _strip_bracketed_no_proxy_entries(value: str) -> str:
    kept = [
        token.strip()
        for token in value.split(",")
        if token.strip() and "[" not in token and "]" not in token
    ]
    return ",".join(kept)


@pytest.fixture(autouse=True)
def _sanitize_no_proxy_env() -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    for name, value in previous.items():
        if value is not None:
            os.environ[name] = _strip_bracketed_no_proxy_entries(value)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
