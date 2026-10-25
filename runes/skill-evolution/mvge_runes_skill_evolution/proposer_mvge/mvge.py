from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.core import DEFAULT_AGENT_NAME, agent_dir

from mvge_runes_skill_evolution.engine import SkillEvolutionEngine
from mvge_runes_skill_evolution.proposer_mvge.spells.finish import (
    get_active_engine,
    make_finish_spell,
    set_active_engine,
)
from mvge_runes_skill_evolution.proposer_mvge.spells.read_file import (
    make_read_file_spell,
)

if TYPE_CHECKING:
    # Annotations only. ``mvge.agent`` must not be imported at module scope:
    # its ``__init__`` imports ``mvge.runes.rune_runner``, which is the loader
    # that loads Rune modules, so a Rune imported during that chain sees a
    # half-built ``mvge.agent`` and the import fails. The one runtime use is
    # in ``create_proposer_mvge``, which imports it locally. See the Rune
    # charter.
    from mvge.agent import Mvge

PROPOSER_DIR = Path(__file__).resolve().parent


@contextmanager
def scoped_proposer_context(
    engine: SkillEvolutionEngine | None = None,
    *,
    evolution_dir: Path | None = None,
    raw_experience_dir: Path | None = None,
    target_skills_dir: Path | None = None,
    project_skills_dir: Path | None = None,
    available_skills: dict[str, Any] | list[Any] | None = None,
    auto_apply: bool = True,
    agent_name: str = DEFAULT_AGENT_NAME,
    # Support legacy param names in tests for seamless migration
    knowledge_dir: Path | None = None,
    raw_knowledge_dir: Path | None = None,
) -> Iterator[SkillEvolutionEngine]:
    """Context manager for scoping a SkillEvolutionEngine to the current
    async context."""
    if engine is None:
        evo_dir = (
            evolution_dir
            or knowledge_dir
            or agent_dir(agent_name) / "skill_evolution"
        )
        raw_dir = (
            raw_experience_dir
            or raw_knowledge_dir
            or agent_dir(agent_name) / "raw_experience"
        )
        skills_dir = target_skills_dir or agent_dir(agent_name) / "skills"
        engine = SkillEvolutionEngine(
            evolution_dir=evo_dir,
            raw_experience_dir=raw_dir,
            target_skills_dir=skills_dir,
            project_skills_dir=project_skills_dir,
            available_skills=available_skills,
            auto_apply=auto_apply,
        )
    prev = get_active_engine()
    set_active_engine(engine)
    try:
        yield engine
    finally:
        set_active_engine(prev)


def create_proposer_mvge(
    engine: SkillEvolutionEngine | None = None,
    api_key: str | None = None,
    provider_name: str | None = None,
    **kwargs: Any,
) -> Mvge:
    """Create a standalone Proposer Mvge instance via file-based construction.

    ``mvge.agent`` is imported here rather than at module scope, for two
    reasons. Building an ``Mvge`` discovers spells from disk, so a module-scope
    import would do that work on every import of this module. And
    ``mvge.agent``'s own ``__init__`` imports the loader that loads Rune
    modules, so a module-scope import would raise if this module were imported
    while ``mvge.agent`` was still half-built. By the time this function runs,
    the package is fully loaded.
    """
    from mvge.agent import Mvge

    spells = kwargs.pop("spells", None)
    if spells is None and engine is not None:
        spells = [make_finish_spell(engine), make_read_file_spell(engine)]
    return Mvge(
        name="proposer_mvge",
        api_key=api_key,
        provider_name=provider_name,
        caller_dir=PROPOSER_DIR,
        spells=spells,
        **kwargs,
    )


_proposer_mvge: Mvge | None = None


def get_proposer_mvge() -> Mvge:
    """The shared proposer Mvge, created on first use.

    Constructing an Mvge discovers spells from disk, which imports and
    reflects on the proposer spells. Doing that at module import made this
    module -- and therefore the whole rune -- unimportable whenever that
    discovery failed, and it also meant merely importing the package built
    an agent, read an API key, and scanned directories. Deferring keeps
    import cheap and side-effect free.
    """
    global _proposer_mvge
    if _proposer_mvge is None:
        _proposer_mvge = create_proposer_mvge()
    return _proposer_mvge


async def run_proposer(
    mvge: Mvge | None = None,
    *,
    engine: SkillEvolutionEngine | None = None,
    evolution_dir: Path | None = None,
    raw_experience_dir: Path | None = None,
    target_skills_dir: Path | None = None,
    project_skills_dir: Path | None = None,
    available_skills: dict[str, Any] | list[Any] | None = None,
    auto_apply: bool = True,
    agent_name: str = DEFAULT_AGENT_NAME,
    user_prompt: str | None = None,
    # Legacy kwargs for compatibility
    knowledge_dir: Path | None = None,
    raw_knowledge_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute the Proposer Mvge to generate a skill proposal."""
    agent = mvge or get_proposer_mvge()

    if engine is None:
        active = get_active_engine()
        if active is not None:
            engine = active
            engine.auto_apply = auto_apply
            if available_skills is not None:
                engine.available_skills = engine._normalize_skills(
                    available_skills
                )
        else:
            evo_dir = (
                evolution_dir
                or knowledge_dir
                or agent_dir(agent_name) / "skill_evolution"
            )
            raw_dir = (
                raw_experience_dir
                or raw_knowledge_dir
                or agent_dir(agent_name) / "raw_experience"
            )
            skills_dir = target_skills_dir or agent_dir(agent_name) / "skills"
            engine = SkillEvolutionEngine(
                evolution_dir=evo_dir,
                raw_experience_dir=raw_dir,
                target_skills_dir=skills_dir,
                project_skills_dir=project_skills_dir,
                available_skills=available_skills,
                auto_apply=auto_apply,
            )

    set_active_engine(engine)
    engine.reset_latest_proposal()

    # Bind spells to engine on the agent instance
    agent._spells = [make_finish_spell(engine), make_read_file_spell(engine)]
    agent.reload_spells()

    if user_prompt is None:
        index_file = engine.evolution_dir / "index.md"
        impact_file = engine.evolution_dir / "skill-impact.md"
        logs_file = engine.evolution_dir / "logs.md"

        index_text = (
            index_file.read_text(encoding="utf-8")
            if index_file.exists()
            else "No index."
        )
        impact_text = (
            impact_file.read_text(encoding="utf-8")
            if impact_file.exists()
            else "No past proposals."
        )
        logs_text = (
            logs_file.read_text(encoding="utf-8")
            if logs_file.exists()
            else "No logs."
        )

        skills_summary = []
        for name, sk in engine.available_skills.items():
            scope = getattr(sk, "scope", None) or (
                sk.get("scope") if isinstance(sk, dict) else "unknown"
            )
            desc = getattr(sk, "description", "") or (
                sk.get("description", "") if isinstance(sk, dict) else ""
            )
            skills_summary.append(f"- {name} [{scope}]: {desc}")
        skills_text = (
            "\n".join(skills_summary) if skills_summary else "None registered."
        )

        user_prompt = (
            f"KNOWLEDGE INDEX:\n{index_text}\n\n"
            f"SKILL IMPACT AUDIT TRAIL:\n{impact_text}\n\n"
            f"AVAILABLE DISCOVERED SKILLS (BY SCOPE):\n{skills_text}\n\n"
            f"TRAINING SUMMARY:\n{logs_text[-2000:]}\n\n"
            "Analyze knowledge patterns and traces. Call finish() with your proposal."
        )

    invocation = await agent.run(user_prompt)

    latest_proposal = engine.get_latest_proposal()
    if latest_proposal is not None:
        return latest_proposal

    response_text = ""
    if hasattr(invocation, "content"):
        content = getattr(invocation, "content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    response_text += block.get("text", "")
                elif isinstance(block, str):
                    response_text += block
        elif isinstance(content, str):
            response_text = content
    elif hasattr(invocation, "text"):
        response_text = getattr(invocation, "text", "")

    # If finish spell wasn't executed directly, extract JSON from invocation
    # text if present
    match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
    )
    if match:
        try:
            parsed = json.loads(match.group(1))
            res = {"success": True, **parsed}
            engine.latest_proposal = res
            return res
        except Exception:  # noqa: BLE001, S110 -- LLM output may not parse; fall through to no_action
            pass

    return {"success": True, "action": "no_action", "content": response_text}


__all__ = [
    "PROPOSER_DIR",
    "create_proposer_mvge",
    "get_proposer_mvge",
    "run_proposer",
    "scoped_proposer_context",
]
