"""Name / path / label / scope validation (spec §6.1, normative order)."""

from __future__ import annotations

from pathlib import Path

#: Windows reserved device names — rejected case-insensitively. The
#: all-lowercase rule already rejects "Con", but lowercase "con" IS a valid
#: identifier and would fail at file-creation time on Windows; the explicit
#: list closes that gap on every platform.
_WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)

_VALID_SCOPES = frozenset({"agent", "user", "project"})


class ValidationError(ValueError):
    """A validation failure carrying the pinned result ``error`` code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_extension_name(name: str, target_dir: Path) -> None:
    """Validate a spell/rune/skill name against ``target_dir``.

    Enforced in order (§6.1):
    1. Reject empty, or containing ``/``, ``\\``, or ``.``.
    2. Require ``str.isidentifier()``, all-lowercase, no leading
       underscore, and not a Windows reserved device name
       (case-insensitive).
    3. After joining, the resolved path must stay inside the resolved
       target dir — fail closed otherwise.

    Raises :class:`ValidationError` with code ``"invalid_name"``.
    """
    if not name or "/" in name or "\\" in name or "." in name:
        raise ValidationError(
            "invalid_name",
            f"invalid extension name {name!r}: must be non-empty and contain "
            "no '/', '\\\\', or '.'",
        )
    if (
        not name.isidentifier()
        or not name.islower()
        or name.startswith("_")
        or name.lower() in _WINDOWS_RESERVED
    ):
        raise ValidationError(
            "invalid_name",
            f"invalid extension name {name!r}: must be a lowercase Python "
            "identifier with no leading underscore and not a Windows "
            "reserved device name",
        )
    base = target_dir.resolve()
    joined = (base / name).resolve()
    if not joined.is_relative_to(base):
        raise ValidationError(
            "invalid_name",
            f"invalid extension name {name!r}: escapes the target directory",
        )


def validate_target_dir(target_dir: str | Path, runes_paths: list[Path]) -> Path:
    """Validate an explicit ``target_dir`` for ``scaffold_rune``.

    Separate from name validation (never apply ``validate_extension_name``
    to a path — a directory necessarily contains ``/``). Normative: the
    target must be absolute and the resolved path must be contained in one
    of the configured runes paths — fail closed otherwise. Containment is
    safety AND correctness: runes load exclusively from configured paths,
    so a non-contained target is dead on arrival.

    Raises :class:`ValidationError` with code ``"outside_runes_paths"``.
    Per the spec §6.3 reuse rule, no distinct ``"invalid_target_dir"``
    code is minted: a relative path is an invalid explicit rune target
    the same way a non-contained one is — never implicit cwd-relative,
    fail closed with the one core code.
    """
    candidate = Path(target_dir)
    if not candidate.is_absolute():
        raise ValidationError(
            "outside_runes_paths",
            f"target_dir {str(target_dir)!r} must be an absolute path inside a "
            "configured runes path — never implicit cwd-relative",
        )
    resolved = candidate.resolve()
    for root in runes_paths:
        if resolved.is_relative_to(root.resolve()):
            return resolved
    raise ValidationError(
        "outside_runes_paths",
        f"target_dir {str(target_dir)!r} is not inside any configured runes path",
    )


def validate_label(label: str) -> None:
    """Validate a model-controlled snapshot label (interpolated into a dir name).

    Raises :class:`ValidationError` with code ``"invalid_label"`` when the
    label contains path separators or starts with a dot.
    """
    if "/" in label or "\\" in label or label.startswith("."):
        raise ValidationError(
            "invalid_label",
            f"invalid snapshot label {label!r}: no path separators or leading dots",
        )


def validate_scope(scope: str) -> str:
    """Validate a ``scaffold_skill`` scope enum.

    Raises :class:`ValidationError` with code ``"invalid_scope"``.
    """
    if scope not in _VALID_SCOPES:
        raise ValidationError(
            "invalid_scope",
            f"invalid scope {scope!r}: must be one of agent, user, project",
        )
    return scope
