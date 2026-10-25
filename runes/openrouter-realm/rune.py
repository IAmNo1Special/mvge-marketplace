from __future__ import annotations

from typing import Any

try:
    from .openrouter import OpenRouterRealm
except (ImportError, ValueError):
    from openrouter import OpenRouterRealm
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook


def openrouter_realm_factory(
    api_key: str = "",
    base_url: str = "https://openrouter.ai/api/v1",
    **kwargs: Any,
) -> OpenRouterRealm:
    """Construct and configure an OpenRouter Realm instance."""
    return OpenRouterRealm(
        api_key=api_key,
        base_url=base_url or "https://openrouter.ai/api/v1",
        **kwargs,
    )


async def before_provider_request(data: Any) -> Any:
    """Hook invoked before dispatching a request to the provider."""
    return data


async def after_provider_response(data: Any) -> Any:
    """Hook invoked after receiving a response from the provider."""
    return data


def rune_factory(api: RuneAPI) -> None:
    """Initialize the OpenRouter Realm extension rune."""
    api.register_realm_factory("openrouter", openrouter_realm_factory)
    api.register_provider(
        "openrouter",
        {"baseUrl": "https://openrouter.ai/api/v1"},
    )
    api.on(SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request)
    api.on(SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response)
