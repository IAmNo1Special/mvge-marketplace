# Spells Specification & Authoring Guide

Spells are native Python callables or `MvgeSpell` classes that provide the agent with deterministic tools.

## Structure & Conventions
- Place each spell in its own module: `spells/<spell_name>.py`.
- The main entry point function must match the file stem: `def <spell_name>(...) -> SpellResult | str:`.
- Use type annotations for all arguments and return types.
- Provide a clean Google-style docstring explaining purpose and parameters (`Args:`).
- Re-export the spell in `spells/__init__.py` and include it in `__all__`.
- Mark read-only spells for plan mode: if the spell only observes and never
  mutates (no writes, no shell execution, no network side effects), decorate
  it with `@read_only` from `spells/markers.py`. Plan mode offers only marked
  spells; mutating spells stay unmarked.

## Read-only classification

| Spell | Read-only | Reason |
| --- | --- | --- |
| `read`, `grep`, `find`, `list_files`, `read_url`, `search_web` | yes | observe only |
| `bash`, `write`, `edit` | no | can mutate the world |

## Example Spell Template
```python
from __future__ import annotations
from mvge_core.spells import SpellResult, SpellStatus


async def my_spell(target: str, count: int = 1) -> SpellResult:
    """Perform a custom operation on a target.

    Args:
        target: The target identifier to process.
        count: Number of iterations.
    """
    try:
        # Perform action
        return SpellResult(
            spell_name="my_spell",
            status=SpellStatus.SUCCESS,
            content=f"Processed {target} x{count}",
        )
    except Exception as exc:
        return SpellResult(
            spell_name="my_spell",
            status=SpellStatus.ERROR,
            error_message=str(exc),
        )
```
