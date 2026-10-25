from mvge_runes_skill_evolution.proposer_mvge.spells.finish import finish
from mvge_runes_skill_evolution.proposer_mvge.spells.read_file import (
    read_file,
)

# Only the two runnable spells. The make_* factories take a
# SkillEvolutionEngine and are bound explicitly by the rune factory, so
# listing them here made spell discovery try to build a pydantic schema for
# that class and fail -- which broke importing this rune outright, because
# the proposer Mvge was constructed at module import time.
__all__ = ["finish", "read_file"]
