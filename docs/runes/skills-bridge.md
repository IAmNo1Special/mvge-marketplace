# Skills Bridge

Official Agent Skills ([agentskills.io](https://agentskills.io)) and Agent Plugins ([agent-plugins.org](https://agent-plugins.org)) bridge for Mvge.

Discovers skill packages across scopes and loads them with progressive disclosure, so the model sees a compact catalog — not the full text of every skill — until it actually needs one.

## Spells

| Spell | Description |
|-------|-------------|
| `activate_skill` | Loads a skill's full content on demand (`<skill_content name="…">`). |

## Progressive disclosure

1. **Tier 1** — session start injects an `<available_skills>` catalog (~50–100 tokens per skill).
2. **Tier 2** — `activate_skill(name)` loads the full skill body when the model asks for it.
3. **Tier 3** — `<skill_resources>` enumerates a skill's bundled resources without eager loading.

Anti-bloat turn updates keep the catalog deduplicated as skills activate.

## Discovery & precedence

Scans `PROJECT`, `USER`, `AGENT`, and `PLUGINS` (`plugin.json`) scopes. Precedence is `PROJECT > USER > AGENT`, deterministic first-wins with shadow tracking. Parsing is tolerant (YAML colon repair, lenient validation).

## Commands

- `mvge skill list` — show discovered skills.
- `mvge skill dedupe` — report shadowed/duplicated skills.

## Hooks

`session_start`, `before_mvge_start`, `context_transform`, `session_shutdown`.

## Dependencies

`pyyaml>=6.0`, `typer>=0.12`.
