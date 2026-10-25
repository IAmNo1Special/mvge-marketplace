from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class SkillScope(StrEnum):
    """Discovery scopes for Agent Skills in precedence order."""

    PROJECT = "project"
    USER = "user"
    AGENT = "agent"


class SkillDiagnosticKind(StrEnum):
    """Diagnostic kinds produced during skill discovery and parsing."""

    SHADOWED_SKILL = "shadowed_skill"
    PARSE_WARNING = "parse_warning"
    MALFORMED_YAML = "malformed_yaml"
    INVALID_PLUGIN = "invalid_plugin"
    PATH_ESCAPE = "path_escape"


@dataclass(frozen=True)
class SkillDiagnostic:
    """A diagnostic recorded during skill discovery or parsing."""

    kind: SkillDiagnosticKind
    skill_name: str
    message: str
    scope: SkillScope | None = None
    path: str = ""


@dataclass
class SkillManifest:
    """Parsed representation of a SKILL.md file adhering to agentskills.io."""

    name: str
    description: str
    scope: SkillScope = SkillScope.PROJECT
    path: str = ""
    location: str = ""
    version: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: str = ""
    disable_model_invocation: bool = False
    body: str | None = None

    def __post_init__(self) -> None:
        if not self.location and self.path:
            if self.path.endswith(("SKILL.md", "skill.md")):
                self.location = self.path
            else:
                self.location = (Path(self.path) / "SKILL.md").as_posix()
        elif self.location and not self.path:
            self.path = self.location

    @property
    def base_dir(self) -> Path:
        loc = self.location or self.path
        if loc.endswith(("SKILL.md", "skill.md")):
            return Path(loc).parent
        return Path(loc)


@dataclass
class SkillLoad:
    """A loaded skill container."""

    manifest: SkillManifest


@dataclass
class PluginManifest:
    """Parsed representation of an Agent Plugin (plugin.json) embedding skills."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    skills: list[str] = field(default_factory=list)
    path: str = ""


@dataclass
class SkillActivationResult:
    """The result of activating a skill."""

    name: str
    content: str
    location: str
    resources: list[str] = field(default_factory=list)
