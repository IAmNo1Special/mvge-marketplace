"""User-owned persistent approval policy.

Policy lives under ~/.agents/approval/policy.toml — user-owned, never
inside the active repository. Writes are atomic (lock + temp file + fsync
+ atomic replace) with user-only permissions.

Malformed TOML semantics per spec:
  recoverable (TOML parses, a rule fails schema validation): drop the rule,
      honor the rest, warn.
  unrecoverable (TOML does not parse): zero allow rules, session switch
      suspended, prompt everything, blocking warning with a repair path.

Install-id binding: the host stamps its install id into the file on every
write. A file stamped with a different id is ignored entirely (with a loud
warning); a parseable file with no stamp is stamped once (migration).
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os
import shlex
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from mvge_runes_approval_rune.contracts import (
    ApprovalRequest,
    Outcome,
    ReasonCode,
    Scope,
    SpellIdentity,
)

POLICY_FILENAME = "policy.toml"
INSTALL_ID_KEY = "install_id"
SUPPORTED_SCHEMA_VERSION = 1

# Explicit per spec: ; | & $ ` ( ) { } < > \r \n — space is allowed.
_FORBIDDEN_RAW = frozenset(
    [";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "\r", "\n"]
)


@dataclass
class Constraint:
    """One argument constraint on an allow rule."""

    field: str
    op: str
    value: Any = None
    values: list[Any] = dataclasses.field(default_factory=list)
    program_glob: str | None = None
    args_glob: str | None = None

    def matches(self, args: dict[str, Any], project_root: str) -> bool:
        """Check the constraint against normalized arguments."""
        if self.op == "exact":
            return (
                self.field in args
                and type(args[self.field]) is type(self.value)
                and args[self.field] == self.value
            )
        if self.op == "one_of":
            return self.field in args and args[self.field] in self.values
        if self.op == "path_within_project":
            return self._path_within_project(args.get(self.field), project_root)
        if self.op == "command_prefix":
            return self._command_prefix(args.get(self.field))
        return False

    @staticmethod
    def _path_within_project(value: Any, project_root: str) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            root = Path(project_root).resolve()
            candidate = (
                (root / value).resolve()
                if not os.path.isabs(value)
                else Path(value).resolve()
            )
        except (OSError, RuntimeError):
            return False
        return candidate == root or root in candidate.parents

    def _command_prefix(self, value: Any) -> bool:
        """POSIX-only program-identity gate, best-effort per spec."""
        if sys.platform == "win32":
            return False
        if not isinstance(value, str) or not value.strip():
            return False
        if any(char in value for char in _FORBIDDEN_RAW):
            return False
        try:
            argv = shlex.split(value, posix=True)
        except ValueError:
            return False
        if not argv:
            return False
        if not self.program_glob or not fnmatch.fnmatchcase(
            argv[0], self.program_glob
        ):
            return False
        if self.args_glob is None:
            return True
        return fnmatch.fnmatchcase(" ".join(argv[1:]), self.args_glob)


@dataclass
class DenyRule:
    """Never-allow rule: matches stable identity, no code digest."""

    spell: SpellIdentity
    project: str | None = None
    id: str = ""

    def matches(self, request: ApprovalRequest) -> bool:
        """Matches."""
        spell = request.spell
        if spell.stable_key() != self.spell.stable_key():
            return False
        if self.project is not None and request.project_root != self.project:
            return False
        return True


@dataclass
class AllowRule:
    """Always-allow rule: identity + digest + scope + constraints must match."""

    spell: SpellIdentity
    constraints: list[Constraint] = dataclasses.field(default_factory=list)
    project: str | None = None
    id: str = ""

    def matches(self, request: ApprovalRequest) -> bool:
        """Matches."""
        if request.schemaless:
            return False
        if request.spell.full_key() != self.spell.full_key():
            return False
        if self.project is not None and request.project_root != self.project:
            return False
        return all(
            c.matches(request.arguments, request.project_root)
            for c in self.constraints
        )


@dataclass
class ProjectApproval:
    """Projectapproval."""

    root: str
    enabled: bool = True


@dataclass
class PolicyOutcome:
    """Policyoutcome."""

    decision: Outcome | Literal["prompt"]
    scope: Scope = "once"
    reason_code: ReasonCode = "rule"
    rule_id: str | None = None
    warning: str | None = None


@dataclass
class Policy:
    """Policy."""

    schema_version: int = SUPPORTED_SCHEMA_VERSION
    always_deny: list[DenyRule] = dataclasses.field(default_factory=list)
    always_allow: list[AllowRule] = dataclasses.field(default_factory=list)
    approved_projects: list[ProjectApproval] = dataclasses.field(
        default_factory=list
    )
    broken: bool = False
    session_suspended: bool = False
    install_mismatch: bool = False
    warnings: list[str] = dataclasses.field(default_factory=list)


def evaluate(
    request: ApprovalRequest,
    policy: Policy,
    session_approved: bool,
) -> PolicyOutcome:
    """Deterministic precedence per spec.

    deny > engine-marked non-runner-origin read_only auto-allow >
    session approve-all > project approval > per-spell allow > prompt.
    """
    for deny_rule in policy.always_deny:
        if deny_rule.matches(request):
            return PolicyOutcome(
                decision="deny",
                scope="spell",
                reason_code="rule",
                rule_id=deny_rule.id or None,
            )
    if request.is_read_only and not request.runner_origin:
        return PolicyOutcome(
            decision="allow", scope="spell", reason_code="read_only"
        )
    if (
        session_approved
        and not request.schemaless
        and not policy.session_suspended
        and not policy.broken
    ):
        return PolicyOutcome(
            decision="allow", scope="session", reason_code="user"
        )
    for approval in policy.approved_projects:
        if (
            approval.enabled
            and not request.schemaless
            and request.project_root == approval.root
        ):
            return PolicyOutcome(
                decision="allow",
                scope="project",
                reason_code="rule",
                rule_id=f"project:{approval.root}",
            )
    for allow_rule in policy.always_allow:
        if allow_rule.matches(request):
            return PolicyOutcome(
                decision="allow",
                scope="spell",
                reason_code="rule",
                rule_id=allow_rule.id or None,
            )
    return PolicyOutcome(decision="prompt", scope="once", reason_code="user")


class MalformedPolicy(Exception):
    """Unrecoverable policy file (does not parse)."""


class PolicyStore:
    """Load/save the user-owned policy file with atomic writes."""

    def __init__(self, data_dir: Path, install_id: str | None) -> None:
        """Initialize the instance."""
        self.data_dir = Path(data_dir)
        self.install_id = install_id

    @property
    def policy_path(self) -> Path:
        """Policy path."""
        return self.data_dir / POLICY_FILENAME

    def read_install_id(self) -> str | None:
        """Read install id."""
        try:
            with open(self.policy_path, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None
        value = data.get(INSTALL_ID_KEY)
        return value if isinstance(value, str) else None

    def save(self, policy: Policy) -> None:
        """Atomic write: lock + temp file + fsync + atomic replace."""
        import tomli_w

        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        document: dict[str, Any] = {
            "schema_version": policy.schema_version,
            "always_deny": [_deny_to_toml(rule) for rule in policy.always_deny],
            "always_allow": [
                _allow_to_toml(rule) for rule in policy.always_allow
            ],
            "approved_projects": [
                {"root": item.root, "enabled": item.enabled}
                for item in policy.approved_projects
            ],
        }
        if self.install_id is not None:
            document[INSTALL_ID_KEY] = self.install_id
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.data_dir), prefix=".policy-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(tomli_w.dumps(document).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.policy_path)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def _migrate_stamp_install_id(self) -> None:
        """Stamp the current install id once onto a parseable unstamped file."""
        try:
            with open(self.policy_path, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return
        data[INSTALL_ID_KEY] = self.install_id
        import tomli_w

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.data_dir), prefix=".policy-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(tomli_w.dumps(data).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.policy_path)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def load_policy(store: PolicyStore) -> Policy:
    """Load the policy, applying malformed-TOML and install-id semantics."""
    policy = Policy()
    if store.install_id is None:
        policy.warnings.append(
            "Approval Rune install-id unavailable: policy treated as empty. "
            "No grants will be applied until the host provides the install id."
        )
        return policy
    try:
        with open(store.policy_path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return policy
    except tomllib.TOMLDecodeError as exc:
        policy.broken = True
        policy.session_suspended = True
        policy.warnings.append(
            "Blocking warning: approval policy.toml is unrecoverable "
            f"({exc}). All allow rules are ignored, the session approve-all "
            "switch is suspended, and every mutating cast will prompt. "
            "Repair or delete the file to restore persistent grants."
        )
        return policy
    except OSError as exc:
        policy.broken = True
        policy.session_suspended = True
        policy.warnings.append(
            f"Blocking warning: approval policy file unreadable ({exc}). "
            "All allow rules are ignored and every mutating cast will prompt."
        )
        return policy

    stored_id = data.get(INSTALL_ID_KEY)
    if not isinstance(stored_id, str):
        # Migration: parseable policy with no stamp — stamp once, proceed.
        store._migrate_stamp_install_id()
    elif stored_id != store.install_id:
        # Different installation: ignore the entire policy, loudly.
        policy.install_mismatch = True
        policy.warnings.append(
            "Approval policy ignored: install-id mismatch. The policy file "
            "belongs to a different installation, so all rules (including "
            "deny rules) are ignored and every mutating cast will prompt. "
            "Review and re-create grants for this installation."
        )
        return policy

    schema_version = data.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if not isinstance(schema_version, int):
        policy.warnings.append(
            "Recoverable: schema_version is not an integer; treating as "
            f"{SUPPORTED_SCHEMA_VERSION}."
        )
        schema_version = SUPPORTED_SCHEMA_VERSION
    policy.schema_version = schema_version
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        policy.warnings.append(
            f"Recoverable: unsupported schema_version {schema_version}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}. Known sections are honored."
        )

    for index, raw in enumerate(_as_list(data.get("always_deny"))):
        try:
            policy.always_deny.append(_parse_deny_rule(raw, index))
        except ValueError as exc:
            policy.warnings.append(
                f"Recoverable: always_deny[{index}] dropped ({exc})."
            )
    for index, raw in enumerate(_as_list(data.get("always_allow"))):
        try:
            policy.always_allow.append(_parse_allow_rule(raw, index))
        except ValueError as exc:
            policy.warnings.append(
                f"Recoverable: always_allow[{index}] dropped ({exc})."
            )
    for index, raw in enumerate(_as_list(data.get("approved_projects"))):
        try:
            policy.approved_projects.append(_parse_project(raw, index))
        except ValueError as exc:
            policy.warnings.append(
                f"Recoverable: approved_projects[{index}] dropped ({exc})."
            )
    return policy


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_spell(raw: Any, where: str, need_digest: bool) -> SpellIdentity:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: spell must be a table")
    fields = ("name", "source_kind", "source_id", "source_scope")
    values: dict[str, str] = {}
    for name in fields:
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{where}: spell.{name} must be a non-empty string"
            )
        values[name] = value
    digest = raw.get("code_digest", "")
    if need_digest and (not isinstance(digest, str) or not digest):
        raise ValueError(
            f"{where}: spell.code_digest is required for allow rules"
        )
    if not isinstance(digest, str):
        raise ValueError(f"{where}: spell.code_digest must be a string")
    return SpellIdentity(
        name=values["name"],
        source_kind=values["source_kind"],
        source_id=values["source_id"],
        source_scope=values["source_scope"],
        code_digest=digest,
    )


def _parse_deny_rule(raw: Any, index: int) -> DenyRule:
    where = f"always_deny[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: rule must be a table")
    spell = _parse_spell(raw.get("spell"), where, need_digest=False)
    project = raw.get("project")
    if project is not None and (not isinstance(project, str) or not project):
        raise ValueError(f"{where}: project must be a non-empty string")
    rule_id = raw.get("id", "")
    if not isinstance(rule_id, str):
        raise ValueError(f"{where}: id must be a string")
    return DenyRule(spell=spell, project=project, id=rule_id or f"deny-{index}")


def _parse_constraint(raw: Any, where: str) -> Constraint:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: constraint must be a table")
    field_name = raw.get("field")
    op = raw.get("op")
    if not isinstance(field_name, str) or not field_name:
        raise ValueError(f"{where}: field must be a non-empty string")
    if op not in ("exact", "one_of", "path_within_project", "command_prefix"):
        raise ValueError(f"{where}: unsupported op {op!r}")
    if op == "exact":
        if "value" not in raw:
            raise ValueError(f"{where}: exact requires value")
        return Constraint(field=field_name, op=op, value=raw["value"])
    if op == "one_of":
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(
                f"{where}: one_of requires a non-empty values list"
            )
        return Constraint(field=field_name, op=op, values=list(values))
    if op == "command_prefix":
        program_glob = raw.get("program_glob")
        if not isinstance(program_glob, str) or not program_glob:
            raise ValueError(f"{where}: command_prefix requires program_glob")
        args_glob = raw.get("args_glob")
        if args_glob is not None and not isinstance(args_glob, str):
            raise ValueError(f"{where}: args_glob must be a string")
        return Constraint(
            field=field_name,
            op=op,
            program_glob=program_glob,
            args_glob=args_glob,
        )
    return Constraint(field=field_name, op=op)


def _parse_allow_rule(raw: Any, index: int) -> AllowRule:
    where = f"always_allow[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: rule must be a table")
    spell = _parse_spell(raw.get("spell"), where, need_digest=True)
    project = raw.get("project")
    if project is not None and (not isinstance(project, str) or not project):
        raise ValueError(f"{where}: project must be a non-empty string")
    constraints = [
        _parse_constraint(item, f"{where}.constraints[{i}]")
        for i, item in enumerate(_as_list(raw.get("constraints")))
    ]
    rule_id = raw.get("id", "")
    if not isinstance(rule_id, str):
        raise ValueError(f"{where}: id must be a string")
    return AllowRule(
        spell=spell,
        constraints=constraints,
        project=project,
        id=rule_id or f"allow-{index}",
    )


def _parse_project(raw: Any, index: int) -> ProjectApproval:
    where = f"approved_projects[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: entry must be a table")
    root = raw.get("root")
    if not isinstance(root, str) or not root:
        raise ValueError(f"{where}: root must be a non-empty string")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{where}: enabled must be a boolean")
    return ProjectApproval(root=root, enabled=enabled)


def _spell_to_toml(spell: SpellIdentity, need_digest: bool) -> dict[str, Any]:
    table: dict[str, Any] = {
        "name": spell.name,
        "source_kind": spell.source_kind,
        "source_id": spell.source_id,
        "source_scope": spell.source_scope,
    }
    if need_digest:
        table["code_digest"] = spell.code_digest
    return table


def _deny_to_toml(rule: DenyRule) -> dict[str, Any]:
    table: dict[str, Any] = {
        "spell": _spell_to_toml(rule.spell, need_digest=False),
        "id": rule.id,
    }
    if rule.project is not None:
        table["project"] = rule.project
    return table


def _constraint_to_toml(constraint: Constraint) -> dict[str, Any]:
    table: dict[str, Any] = {"field": constraint.field, "op": constraint.op}
    if constraint.op == "exact":
        table["value"] = constraint.value
    elif constraint.op == "one_of":
        table["values"] = list(constraint.values)
    elif constraint.op == "command_prefix":
        table["program_glob"] = constraint.program_glob
        if constraint.args_glob is not None:
            table["args_glob"] = constraint.args_glob
    return table


def _allow_to_toml(rule: AllowRule) -> dict[str, Any]:
    table: dict[str, Any] = {
        "spell": _spell_to_toml(rule.spell, need_digest=True),
        "constraints": [_constraint_to_toml(c) for c in rule.constraints],
        "id": rule.id,
    }
    if rule.project is not None:
        table["project"] = rule.project
    return table
