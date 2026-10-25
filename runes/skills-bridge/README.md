# skills-bridge Rune

Official **Agent Skills ([agentskills.io](https://agentskills.io))** and **Agent Plugins ([agent-plugins.org](https://agent-plugins.org))** bridge for Mvge.

## Features
- **Three-tier progressive disclosure**:
  - Tier 1: Injects `<available_skills>` catalog at session start (~50-100 tokens/skill).
  - Tier 2: Dedicated `activate_skill(name: str)` spell loads `<skill_content name="...">` on demand.
  - Tier 3: Enumerates `<skill_resources>` without eager loading.
- **Discovery**: Scans `PROJECT`, `USER`, `AGENT`, and `PLUGINS` (`plugin.json`).
- **Precedence**: `PROJECT > USER > AGENT`. Deterministic first-wins with shadow tracking.
- **Tolerant Parsing**: Resilient YAML colon repair and lenient validation.
- **Context Protection**: Anti-bloat regex turn updates; deduplicated activations.
- **CLI Commands**: `mvge skill list`, `mvge skill dedupe`, `mvge skill validate`, `mvge skill show`.
