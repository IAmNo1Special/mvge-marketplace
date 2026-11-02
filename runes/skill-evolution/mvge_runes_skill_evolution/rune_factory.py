from __future__ import annotations

# Mvge implementation of WikiSkill (arxiv:2608.27454) — persistent experience
# consolidation and autonomous skill evolution.
# Three-layer: raw_experience -> skill_evolution -> skills (SKILL.md+PURPOSE.md).
import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import (
    RuneContext,
    SigilHook,
)

from mvge_runes_skill_evolution.consolidator.consolidator import (
    ExperienceConsolidator,
)
from mvge_runes_skill_evolution.consolidator.harvester import (
    ExperienceHarvester,
)
from mvge_runes_skill_evolution.engine import SkillEvolutionEngine
from mvge_runes_skill_evolution.hooks.handlers import SkillEvolutionHooks
from mvge_runes_skill_evolution.proposer_mvge import (
    create_proposer_mvge,
    run_proposer,
)
from mvge_runes_skill_evolution.queries import SkillEvolutionQueries
from mvge_runes_skill_evolution.spells import (
    make_consolidate_spell,
    make_export_spell,
)
from mvge_runes_skill_evolution.store import SkillEvolutionStore

if TYPE_CHECKING:
    # Mvge is an annotation only in this module, and this file has
    # ``from __future__ import annotations``, so it is never evaluated at
    # runtime. ``mvge.agent`` must not be imported at module scope: its
    # ``__init__`` imports ``mvge.runes.rune_runner``, which is the loader
    # that loads Rune modules, so a Rune imported during that chain sees a
    # half-built ``mvge.agent`` and the import fails. See the Rune charter.
    from mvge.agent import Mvge


class SkillEvolutionRune:
    """Public instance object returned by the skill-evolution factory.

    Host-side state surface (store, consolidator, engine, proposer
    subagent), replacing the old runner-attribute stash. Reachable
    only through the engine-owned runner's get_rune, never RuneAPI.
    """

    def __init__(
        self,
        store: SkillEvolutionStore,
        consolidator: ExperienceConsolidator,
        harvester: ExperienceHarvester,
        engine: SkillEvolutionEngine,
        subagent: Any,
    ) -> None:
        self.store = store
        self.consolidator = consolidator
        self.harvester = harvester
        self.engine = engine
        self.subagent = subagent


def rune_factory(api: RuneAPI) -> SkillEvolutionRune:
    context: RuneContext = api.context
    agent_name = context.agent_name or "coding_mvge"
    # Phase 2A: the proposer authenticates through Mvge's own
    # credential resolution (explicit -> realm env -> MVGE_API_KEY).
    # The parent session's key is never read from the rune context.
    proposer_api_key = None
    config_base = Path(f"~/.agents/agents/{agent_name}").expanduser()

    # Workspace-aware partitioning in agent scope to prevent cross-project pollution
    cwd_path = Path(context.cwd).resolve() if context.cwd else None
    is_project = bool(
        cwd_path
        and (
            (cwd_path / ".agents").is_dir()
            or (cwd_path / ".git").is_dir()
            or (cwd_path / "pyproject.toml").is_file()
        )
    )

    if is_project and cwd_path:
        clean_name = (
            re.sub(r"[^a-zA-Z0-9_-]", "_", cwd_path.name) or "workspace"
        )
        path_hash = hashlib.sha256(str(cwd_path).encode("utf-8")).hexdigest()[
            :8
        ]
        ws_slug = f"{clean_name}-{path_hash}"
        evolution_dir = config_base / "workspaces" / ws_slug / "skill_evolution"
        raw_experience_dir = (
            config_base / "workspaces" / ws_slug / "raw_experience"
        )
        project_skills_dir: Path | None = cwd_path / ".agents" / "skills"
    else:
        evolution_dir = config_base / "skill_evolution"
        raw_experience_dir = config_base / "raw_experience"
        project_skills_dir = None

    raw_experience_dir.mkdir(parents=True, exist_ok=True)
    target_skills_dir = config_base / "skills"
    store = SkillEvolutionStore(evolution_dir)
    store.bind_raw_experience(raw_experience_dir)
    queries = SkillEvolutionQueries(store)
    harvester = ExperienceHarvester(
        max_buffer_size=100,
        persist_path=evolution_dir / "harvester_buffer.json",
    )
    # Phase 2A: consolidation model calls go through the engine-held
    # capability. The adapter renders the message list to the prompt
    # the capability takes; no key or realm object reaches this rune.
    async def _complete_via_engine(
        messages: list[dict[str, str]], signal: Any = None
    ) -> str:
        prompt = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
        )
        return await api.stream_model_text(
            "openrouter/auto", prompt, temperature=0.3, max_tokens=4000
        )

    consolidator = ExperienceConsolidator(
        store=store,
        queries=queries,
        harvester=harvester,
        batch_size=20,
        interval_turns=5,
        complete_fn=_complete_via_engine,
        llm_model="openrouter/auto",
    )
    engine = SkillEvolutionEngine(
        evolution_dir=evolution_dir,
        raw_experience_dir=raw_experience_dir,
        target_skills_dir=target_skills_dir,
        project_skills_dir=project_skills_dir,
        available_skills=api.get_skills(),
        auto_apply=True,
    )
    subagent_mvge = create_proposer_mvge(engine=engine, api_key=proposer_api_key)
    subagent_mvge.event_bus.subscribe(
        lambda ev: api.emit_event("mvge_event", ev)
    )

    class SubagentRunner:
        def __init__(self, mvge_inst: Mvge, eng: SkillEvolutionEngine) -> None:
            self.mvge = mvge_inst
            self.engine = eng
            self.complete_fn: Any = None
            self.llm_model: str = "openrouter/auto"

        async def run(self, auto_apply: bool = True) -> dict[str, Any]:
            self.engine.auto_apply = auto_apply
            return await run_proposer(
                mvge=self.mvge,
                engine=self.engine,
                auto_apply=auto_apply,
            )

    subagent = SubagentRunner(subagent_mvge, engine)
    hooks = SkillEvolutionHooks(
        harvester=harvester,
        consolidator=consolidator,
        store=store,
        subagent=subagent,
    )
    hooks.bind_raw(raw_experience_dir)
    hooks.register(api)
    api.register_spell(make_consolidate_spell(consolidator))
    api.register_spell(make_export_spell(store, agent_name, api.get_skills))
    api.register_command(
        name="skill-evolution-consolidate",
        description="Force skill evolution experience consolidation",
        handler=lambda _: api.send_message(
            "[skill_evolution] Consolidation triggered"
        ),
    )
    api.register_command(
        name="skill-evolution-export",
        description="Export skills as plugin",
        handler=lambda _: api.send_message(
            "[skill_evolution] Export initiated"
        ),
    )
    api.register_command(
        name="skill-evolution-stats",
        description="Show skill evolution stats",
        handler=lambda _: api.send_message(
            "[skill_evolution] Stats requested"
        ),
    )

    async def handle_propose(args: Any = None) -> None:
        api.send_message(
            "[skill_evolution] Skill Proposer sub-agent launched..."
        )
        res = await subagent.run(auto_apply=True)
        if res.get("success"):
            action = res.get("action", "no_action")
            name = res.get("name", "")
            api.send_message(
                f"[skill_evolution] Skill proposal complete: {action} {name}".strip()
            )
        else:
            api.send_message(
                f"[skill_evolution] Skill proposal failed: {res.get('error')}"
            )

    api.register_command(
        name="skill-evolution-propose",
        description="Run autonomous Skill Proposer sub-agent",
        handler=handle_propose,
    )
    # Phase 2A: the factory returns a public instance object instead
    # of stashing state on the runner. The engine records it under the
    # manifest name, reachable host-side via RuneRunner.get_rune;
    # it is never exposed through RuneAPI.
    return SkillEvolutionRune(
        store=store,
        consolidator=consolidator,
        harvester=harvester,
        engine=engine,
        subagent=subagent,
    )

    async def on_agent_start(data: Any) -> None:
        pass

    api.on(SigilHook.AGENT_START, on_agent_start)


def set_llm_client(
    rune: Any, complete_fn: Any, model: str = "openrouter/auto"
) -> None:
    target = getattr(rune, "consolidator", None)
    if target is not None:
        target.complete_fn = complete_fn
        target.llm_model = model
    sub = getattr(rune, "subagent", None)
    if sub is not None:
        sub.complete_fn = complete_fn
        sub.llm_model = model
