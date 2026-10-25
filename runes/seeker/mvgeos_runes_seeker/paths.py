"""The one place Seeker derives a search root from the ``.agents`` layer.

Four of these roots used to be relative literals spelled out inside the
Rune: ``.agents/.mvgeos/spells``, ``.agents/.mvgeos/skills``,
``.agents/.mvgeos/extensions`` and ``.agents/.mvgeos/runes``. That layer
is dead. ADR-0002 chose it, ADR-0014 superseded it, and no first-party code
in either repository has ever written there -- so every one of those
searches matched nothing on a correctly installed system.

Each resolver here is a thin wrapper over the matching ``mvge.core``
resolver, and each is a *call* rather than a module constant. That second
part matters as much as the first: the layer is read when the path is asked
for, so a Rune loaded once at startup still follows a layer relocated by
``$MVGEOS_GLOBAL_DIR`` afterwards. ``mvge.core.layers`` states the reason
in one line -- the install path and the discovery path resolving
differently is what allows a Rune to install successfully and then load
nowhere.

``~/.claude/skills`` is listed by :func:`skill_roots` and is not this
layer. It is a third-party convention, so relocating our own directory
does not relocate it and it is not ours to derive.

``.agents/skills`` *is* this layer -- the project one, the sibling of
``.agents/extensions``. It used to be spelled out here as a relative
constant, ``Path(".agents") / "skills"``, with no project attached, which
bound it to the working directory at the moment of the call: launched
from an unrelated checkout, Seeker searched that checkout's Skills. It is
routed through ``mvge.core.project_skills_dir`` and reached only when a
caller *names* a project, on the same terms ADR 0017 sets for Runes --
anchored, never ambient. See ADR 0017.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from mvge.core import (
    agent_spells_dir,
    extensions_dir,
    project_skills_dir,
    skills_dir,
)


def spells_root(agent_name: str) -> Path:
    """The agent-scope Spells directory: ``<global>/agents/<name>/spells``.

    Routed through ``mvge.core.agent_spells_dir``, which the engine added
    for exactly this Rune's reason: the host spells this path out in its
    installer and in its own Spell discovery, and a Rune that searches it
    must not spell out a third copy. This function used to compose the path
    from ``agent_dir(name) / "spells"`` because the engine exported no
    resolver for it. It does now, so the local derivation is a second copy
    of a directory the engine owns, and the comment that justified it had
    become false.
    """
    return agent_spells_dir(agent_name)


def extensions_root() -> Path:
    """The user-scope Rune directory ``mcp_search`` scans: ``<global>/extensions``."""
    return extensions_dir()


def skill_roots(project_dir: str | Path | None = None) -> list[Path]:
    """Every skills directory Seeker searches, resolved now and unfiltered.

    Order is precedence. The user-scope layer leads because it is ours,
    then the third-party convention, then the project layer.

    ``project_dir`` names the project to search. ``None`` means no project
    layer at all -- not the current directory. That is the whole point of
    the parameter being explicit: ``mvge.core.layers`` omits the project
    layer rather than defaulting it, so the ambient working directory
    cannot leak into a search path. A Skill is instructions the Mvge will
    follow, so a root bound to an unnamed checkout does not return the
    wrong answer, it changes what the agent does. See ADR 0017.
    """
    roots = [
        skills_dir(),
        Path.home() / ".claude" / "skills",
    ]
    if project_dir is not None:
        roots.append(project_skills_dir(project_dir))
    return roots


def missing(roots: Iterable[Path]) -> list[Path]:
    """Which of ``roots`` do not exist, so a silent empty search can name them.

    A search that returns nothing because its root does not exist is
    indistinguishable from one that found nothing. That indistinguishability
    is how a dead layer stays invisible for a release, so each Spell reports
    the roots it could not search rather than returning a bare zero.
    """
    return [root for root in roots if not root.is_dir()]
