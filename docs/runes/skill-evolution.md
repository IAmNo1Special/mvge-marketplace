# Skill Evolution

Persistent experience consolidation and autonomous skill evolution for Mvge,
implementing the WikiSkill approach: raw experience is consolidated into durable
patterns, and those patterns are proposed and applied as agent-scoped skills.

## Install

```bash
mvge rune install skill-evolution
```

It is no longer bundled with `coding_mvge`. A coding session gets skill
evolution only once this rune is installed.

## How it works

- **Harvest** — `consolidator/harvester.py` reads raw experience and session
  traces from `raw_experience/`.
- **Consolidate** — `consolidator/` turns those into durable patterns and an
  index under `skill_evolution/`. Runs on `turn_end` and `after_invocation`.
- **Propose** — a dedicated proposer Mvge (`proposer_mvge/`) with the `finish`
  and `read_file` spells reviews the patterns and emits a proposal, either
  creating a new skill or patching an existing one.
- **Apply** — accepted proposals are written to the agent's `skills/`
  directory, when `auto_apply` is on.

Hooks: `session_start`, `after_invocation`, `turn_end`, `session_shutdown`.

## State

Everything is agent-scoped, under the global layer:

- `<global>/agents/<agent>/raw_experience/` — harvested input
- `<global>/agents/<agent>/skill_evolution/` — patterns, index, audit trail

## Dependencies

This rune depends on the engine only. Skill discovery goes through
`RuneAPI.get_skills()`, so skills resolve as runtime data supplied by whatever
rune registered them — `skills-bridge`, in practice. It imports no other rune's
modules, and it deliberately ships no dependency on one.

## Notes

Two defects were fixed when this left `coding_mvge`:

- The proposer's spell package listed its `make_*` spell factories in
  `__all__`. Spell discovery coerces everything listed there, tried to build a
  pydantic schema for the `SkillEvolutionEngine` parameter, and failed — which
  made the rune unimportable.
- The proposer Mvge was constructed at module import time, so any discovery
  failure broke `import` of the whole rune rather than a single code path. It
  is now created on first use.