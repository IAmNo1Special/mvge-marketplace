"""Plan-mode read-only markings on the coding_mvge spell surface.

Plan mode offers only spells marked ``read_only``. The read-only set is a
deliberate classification: read, grep, find, list_files, read_url, and
search_web observe without mutating; bash, write, and edit can change the
world and stay out of plan mode.
"""

from __future__ import annotations

from coding_mvge.spells import (
    bash,
    edit,
    find,
    grep,
    list_files,
    read,
    read_url,
    search_web,
    write,
)
from mvge.agent.function_spell import coerce_spell

READ_ONLY_SPELLS = (read, grep, find, list_files, read_url, search_web)
MUTATING_SPELLS = (bash, write, edit)


def test_read_only_spells_are_marked() -> None:
    for spell in READ_ONLY_SPELLS:
        assert getattr(spell, "read_only", False) is True, spell.__name__


def test_mutating_spells_are_not_marked_read_only() -> None:
    for spell in MUTATING_SPELLS:
        assert getattr(spell, "read_only", False) is False, spell.__name__


def test_coerced_spells_carry_the_marking() -> None:
    for spell in READ_ONLY_SPELLS:
        assert coerce_spell(spell).read_only is True, spell.__name__
    for spell in MUTATING_SPELLS:
        assert coerce_spell(spell).read_only is False, spell.__name__


def test_read_only_decorator_marks_and_preserves_function() -> None:
    """The @read_only decorator sets the marker without changing behavior."""
    from coding_mvge.spells.markers import read_only

    async def sample_spell() -> str:
        """Sample docstring."""
        return "ok"

    decorated = read_only(sample_spell)
    assert decorated is sample_spell
    assert getattr(decorated, "read_only", False) is True
    assert decorated.__name__ == "sample_spell"
    assert decorated.__doc__ == "Sample docstring."
