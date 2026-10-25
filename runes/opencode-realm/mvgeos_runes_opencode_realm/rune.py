"""The Sigil surface of the OpenCode Zen realm.

Kept separate from :mod:`realm` so the Realm implementation carries no knowledge
of the Rune lifecycle. This module is what the engine loads: it registers the
realm factory and the provider, and hooks the two provider sigils.
"""

from __future__ import annotations

from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

from mvgeos_runes_opencode_realm.realm import ZEN_BASE_URL, OpenCodeRealm


def opencode_realm_factory(
    api_key: str = "",
    base_url: str = ZEN_BASE_URL,
    **kwargs: Any,
) -> OpenCodeRealm:
    """Construct and configure an OpenCode Zen Realm instance.

    The base URL falls back to Zen when a caller passes an empty string, because
    an empty base URL is what an unset config field looks like, and a Realm with
    no base URL cannot build a request URL at all.
    """
    return OpenCodeRealm(
        api_key=api_key,
        base_url=base_url or ZEN_BASE_URL,
        **kwargs,
    )


async def before_provider_request(data: Any) -> Any:
    """Hook invoked before dispatching a request to the provider."""
    return data


async def after_provider_response(data: Any) -> Any:
    """Hook invoked after receiving a response from the provider."""
    return data


def rune_factory(api: RuneAPI) -> None:
    """Initialize the OpenCode Zen Realm extension rune.

    The registered provider config is what lets a bare slug resolve with no
    catalog entry: the engine asks the registry whether it knows the ``opencode``
    prefix, finds this registration, and reads the base URL from here. Without it
    the factory would still register, but an unknown slug would be reported as an
    unknown model rather than resolved.
    """
    api.register_realm_factory("opencode", opencode_realm_factory)
    api.register_provider(
        "opencode",
        {"baseUrl": ZEN_BASE_URL},
    )
    api.on(SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request)
    api.on(SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response)
