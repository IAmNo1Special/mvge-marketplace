# skill-evolution

Persistent experience consolidation and autonomous skill evolution for Mvge.
An implementation of the WikiSkill approach: raw experience is consolidated into
patterns, and patterns are proposed and applied as agent-scoped skills.

## What it does

- **Harvests** raw experience and session traces from `raw_experience/`.
- **Consolidates** them into durable patterns under `skill_evolution/`.
- **Proposes** new or patched skills from those patterns, via a dedicated
  proposer Mvge with the `finish` and `read_file` spells.
- **Applies** accepted proposals to the agent's `skills/` directory.

State lives under the agent scope:
`<global>/agents/<agent>/skill_evolution/` for patterns and
`<global>/agents/<agent>/raw_experience/` for raw input.

## Installation

```bash
mvge rune install skill-evolution
```

It is no longer bundled with `coding_mvge`. Install it explicitly if you want
skill evolution in a coding session.

## Skills

Skill discovery goes through `RuneAPI.get_skills()`, so skills appear only
while a skill-producing rune such as `skills-bridge` is installed and has
registered them. This rune depends on no other rune.
