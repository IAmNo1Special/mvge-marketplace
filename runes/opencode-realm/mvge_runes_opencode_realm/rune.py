"""The Sigil surface of the OpenCode Zen and Go realms.

Kept separate from :mod:`realm` so the Realm implementation carries no knowledge
of the Rune lifecycle. This module is what the engine loads: it registers the
realm factories and the providers, and hooks the two provider sigils.

One Rune provides both products, as two provider entries. ``opencode`` is Zen,
``opencode-go`` is Go, both share the one ``OPENCODE_API_KEY``, and the
provider name is the billing lane: there is no fallback from one to the other.
"""

from __future__ import annotations

from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

from mvge_runes_opencode_realm.realm import (
    GO_BASE_URL,
    ZEN_BASE_URL,
    OpenCodeRealm,
)


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


def opencode_go_realm_factory(
    api_key: str = "",
    base_url: str = GO_BASE_URL,
    **kwargs: Any,
) -> OpenCodeRealm:
    """Construct and configure an OpenCode Go Realm instance.

    The base URL falls back to Go, never to Zen, when a caller passes an empty
    string, for the same reason the Zen factory falls back to Zen: an empty
    base URL is what an unset config field looks like, and a Go Realm pointed
    at Zen would bill the wrong product.
    """
    return OpenCodeRealm(
        api_key=api_key,
        base_url=base_url or GO_BASE_URL,
        realm_name="opencode-go",
        **kwargs,
    )


async def before_provider_request(data: Any) -> Any:
    """Hook invoked before dispatching a request to the provider."""
    return data


async def after_provider_response(data: Any) -> Any:
    """Hook invoked after receiving a response from the provider."""
    return data


def rune_factory(api: RuneAPI) -> None:
    """Initialize the OpenCode Zen and Go Realm extension rune.

    The registered provider configs are what let a bare slug resolve with no
    catalog entry: the engine asks the registry whether it knows the
    ``opencode`` or ``opencode-go`` prefix, finds the matching registration,
    and reads the base URL from there. Without a registration the factory
    would still register, but an unknown slug would be reported as an
    unknown model rather than resolved.
    """
    api.register_realm_factory("opencode", opencode_realm_factory)
    api.register_provider(
        "opencode",
        {"baseUrl": ZEN_BASE_URL},
    )
    api.register_realm_factory("opencode-go", opencode_go_realm_factory)
    api.register_provider(
        "opencode-go",
        {"baseUrl": GO_BASE_URL},
    )
    api.on(SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request)
    api.on(SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response)
