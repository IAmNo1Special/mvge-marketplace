"""Install-time consent end to end for first-party runes.

Rerun 13 (mvge 0.2.49) proved a fresh install could never start a model
turn: the Phase 2B capability gate granted nothing because no host ever
recorded consent, so no realm factory ever registered — even though the
realm rune was installed on disk. Installing a rune is the consent act,
and the session runner reads the file-backed store. This test pins that
path with a real first-party rune instead of a synthetic one: install
``pollinations-realm`` from this tree, load it through ``RuneLifecycle``,
and the ``pollinations`` realm factory must be registered.

The second test pins the other half: consent is what grants, not the
install directory. A rune installed without a consent record (the shape
every 0.2.44-0.2.49 install left behind) registers nothing until the
operator re-consents with ``mvge rune consent``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mvge.agent.rune_lifecycle import RuneLifecycle
from mvge.core.layers import ResolvedLayer, Scope
from mvge.runes.capabilities import default_capability_store_dir
from mvge.runes.installer import install_rune

try:
    from mvge.runes.installer import consent_rune
except ImportError:  # Engine predates install-time consent.
    pytest.skip(
        "engine predates install-time consent",
        allow_module_level=True,
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
REALM_RUNE = REPO_ROOT / "runes" / "pollinations-realm"


def _lifecycle(tmp_path: Path) -> RuneLifecycle:
    return RuneLifecycle(
        agent_name="tester",
        rune_layers=[
            ResolvedLayer(Scope.USER, tmp_path / "extensions"),
        ],
    )


@pytest.mark.asyncio
async def test_install_then_load_registers_realm_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install from this tree, then load: the realm factory registers."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))

    install_rune(str(REALM_RUNE), target_dir=tmp_path / "extensions")

    runner = await _lifecycle(tmp_path).load()

    assert "pollinations" in runner.get_registered_realm_factories()
    assert "pollinations" in runner.get_registered_providers()


@pytest.mark.asyncio
async def test_unconsented_install_registers_nothing_until_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rune with no consent record registers nothing; explicit consent
    is the remedy, and the next load registers the factory."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))

    install_rune(str(REALM_RUNE), target_dir=tmp_path / "extensions")
    # Simulate a pre-fix install: the rune is on disk, no grant exists.
    consents = default_capability_store_dir() / "capability-consents.json"
    assert consents.is_file()
    consents.unlink()

    runner = await _lifecycle(tmp_path).load()
    assert runner.get_registered_realm_factories() == {}
    assert runner.get_registered_providers() == {}

    granted = consent_rune("pollinations-realm", target_dir=tmp_path / "extensions")
    assert granted == ["realm", "hook"]

    runner = await _lifecycle(tmp_path).load()
    assert "pollinations" in runner.get_registered_realm_factories()
