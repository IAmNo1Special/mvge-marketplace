from __future__ import annotations

from typing import Any

try:
    from .pollinations import PollinationsRealm
except (ImportError, ValueError):
    from pollinations import PollinationsRealm
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook


def pollinations_realm_factory(
    api_key: str = "",
    base_url: str = "https://text.pollinations.ai/openai",
    **kwargs: Any,
) -> PollinationsRealm:
    """Construct and configure a Pollinations Realm instance.

    The key defaults to empty on purpose: Pollinations' anonymous tier
    needs no key, and this Realm exists as a bootstrap fallback. A key,
    when one is supplied later, is forwarded as a bearer token.
    """
    return PollinationsRealm(
        api_key=api_key,
        base_url=base_url or "https://text.pollinations.ai/openai",
        **kwargs,
    )


async def before_provider_request(data: Any) -> Any:
    """Hook invoked before dispatching a request to the provider."""
    return data


async def after_provider_response(data: Any) -> Any:
    """Hook invoked after receiving a response from the provider."""
    return data


def rune_factory(api: RuneAPI) -> None:
    """Initialize the Pollinations Realm extension rune."""
    api.register_realm_factory("pollinations", pollinations_realm_factory)
    api.register_provider(
        "pollinations",
        {"baseUrl": "https://text.pollinations.ai/openai"},
    )
    api.on(SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request)
    api.on(SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response)
