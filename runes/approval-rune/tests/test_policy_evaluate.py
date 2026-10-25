"""Tests for policy precedence and argument constraints."""

from typing import Any

from mvge_runes_approval_rune.contracts import ApprovalRequest, SpellIdentity
from mvge_runes_approval_rune.normalization import digest_arguments
from mvge_runes_approval_rune.policy import (
    AllowRule,
    Constraint,
    DenyRule,
    Policy,
    ProjectApproval,
    evaluate,
)


def _identity(name: str = "write", digest: str = "sha256:abc") -> SpellIdentity:
    return SpellIdentity(
        name=name,
        source_kind="builtin",
        source_id="coding_mvge",
        source_scope="agent",
        code_digest=digest,
    )


def _request(
    name: str = "write",
    digest: str = "sha256:abc",
    args: dict[str, Any] | None = None,
    read_only: bool = False,
    runner_origin: bool = False,
    project: str = "/workspace/proj",
) -> ApprovalRequest:
    arguments = args if args is not None else {"path": "/workspace/proj/a.txt"}
    return ApprovalRequest(
        cast_id="call_1",
        spell=_identity(name, digest),
        arguments=arguments,
        argument_digest=digest_arguments(arguments),
        project_root=project,
        tome_id="tome-1",
        session_id="sess-1",
        is_read_only=read_only,
        runner_origin=runner_origin,
        schemaless=False,
    )


def test_deny_beats_everything(tmp_path: Any) -> None:
    """Test deny beats everything."""
    policy = Policy(
        always_deny=[DenyRule(spell=_identity())],
        approved_projects=[ProjectApproval(root="/workspace/proj")],
    )
    outcome = evaluate(_request(), policy, session_approved=True)
    assert outcome.decision == "deny"
    assert outcome.reason_code == "rule"


def test_read_only_auto_allow_requires_non_runner_origin() -> None:
    """Test read only auto allow requires non runner origin."""
    policy = Policy()
    request = _request(name="read", read_only=True, runner_origin=False)
    outcome = evaluate(request, policy, session_approved=False)
    assert outcome.decision == "allow"
    assert outcome.reason_code == "read_only"


def test_read_only_runner_origin_prompts() -> None:
    """Test read only runner origin prompts."""
    policy = Policy()
    request = _request(name="read", read_only=True, runner_origin=True)
    outcome = evaluate(request, policy, session_approved=False)
    assert outcome.decision == "prompt"


def test_read_only_deny_still_wins() -> None:
    """Test read only deny still wins."""
    policy = Policy(always_deny=[DenyRule(spell=_identity("read"))])
    request = _request(name="read", read_only=True, runner_origin=False)
    outcome = evaluate(request, policy, session_approved=False)
    assert outcome.decision == "deny"


def test_session_approval_allows() -> None:
    """Test session approval allows."""
    policy = Policy()
    outcome = evaluate(_request(), policy, session_approved=True)
    assert outcome.decision == "allow"
    assert outcome.scope == "session"


def test_project_approval_allows() -> None:
    """Test project approval allows."""
    policy = Policy(approved_projects=[ProjectApproval(root="/workspace/proj")])
    outcome = evaluate(_request(), policy, session_approved=False)
    assert outcome.decision == "allow"
    assert outcome.scope == "project"


def test_project_approval_requires_canonical_root_match() -> None:
    """Test project approval requires canonical root match."""
    policy = Policy(approved_projects=[ProjectApproval(root="/workspace/proj")])
    outcome = evaluate(
        _request(project="/workspace/other"), policy, session_approved=False
    )
    assert outcome.decision == "prompt"


def test_project_approval_disabled_does_not_allow() -> None:
    """Test project approval disabled does not allow."""
    policy = Policy(
        approved_projects=[
            ProjectApproval(root="/workspace/proj", enabled=False)
        ]
    )
    outcome = evaluate(_request(), policy, session_approved=False)
    assert outcome.decision == "prompt"


def test_allow_rule_requires_digest_match() -> None:
    """Test allow rule requires digest match."""
    policy = Policy(
        always_allow=[
            AllowRule(spell=_identity(digest="sha256:abc"), constraints=[])
        ]
    )
    outcome = evaluate(_request(), policy, session_approved=False)
    assert outcome.decision == "allow"
    changed = evaluate(
        _request(digest="sha256:changed"), policy, session_approved=False
    )
    assert changed.decision == "prompt"


def test_allow_rule_project_scope() -> None:
    """Test allow rule project scope."""
    policy = Policy(
        always_allow=[
            AllowRule(
                spell=_identity(), project="/workspace/proj", constraints=[]
            )
        ]
    )
    outcome = evaluate(_request(), policy, session_approved=False)
    assert outcome.decision == "allow"
    other = evaluate(
        _request(project="/workspace/other"), policy, session_approved=False
    )
    assert other.decision == "prompt"


def test_broken_policy_prompts_everything() -> None:
    """Test broken policy prompts everything."""
    policy = Policy(broken=True, session_suspended=True)
    outcome = evaluate(_request(), policy, session_approved=True)
    assert outcome.decision == "prompt"


def test_schemaless_never_matches_allow_rule() -> None:
    """Test schemaless never matches allow rule."""
    policy = Policy(always_allow=[AllowRule(spell=_identity(), constraints=[])])
    request = _request()
    request_schemaless = ApprovalRequest(
        cast_id=request.cast_id,
        spell=request.spell,
        arguments=request.arguments,
        argument_digest=request.argument_digest,
        project_root=request.project_root,
        tome_id=request.tome_id,
        session_id=request.session_id,
        is_read_only=request.is_read_only,
        runner_origin=request.runner_origin,
        schemaless=True,
    )
    outcome = evaluate(request_schemaless, policy, session_approved=False)
    assert outcome.decision == "prompt"


def test_exact_constraint() -> None:
    """Test exact constraint."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[
            Constraint(field="path", op="exact", value="/workspace/proj/a.txt")
        ],
    )
    policy = Policy(always_allow=[rule])
    assert (
        evaluate(_request(), policy, session_approved=False).decision == "allow"
    )
    mismatch = evaluate(
        _request(args={"path": "/workspace/proj/b.txt"}),
        policy,
        session_approved=False,
    )
    assert mismatch.decision == "prompt"


def test_exact_constraint_requires_strict_type() -> None:
    """Test exact constraint requires strict type."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[Constraint(field="count", op="exact", value=3)],
    )
    policy = Policy(always_allow=[rule])
    outcome = evaluate(
        _request(args={"count": "3"}), policy, session_approved=False
    )
    assert outcome.decision == "prompt"


def test_one_of_constraint() -> None:
    """Test one of constraint."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[
            Constraint(field="mode", op="one_of", values=["fast", "slow"])
        ],
    )
    policy = Policy(always_allow=[rule])
    assert (
        evaluate(
            _request(args={"mode": "fast"}), policy, session_approved=False
        ).decision
        == "allow"
    )
    assert (
        evaluate(
            _request(args={"mode": "evil"}), policy, session_approved=False
        ).decision
        == "prompt"
    )


def test_path_within_project_allows_inside() -> None:
    """Test path within project allows inside."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[Constraint(field="path", op="path_within_project")],
    )
    policy = Policy(always_allow=[rule])
    assert (
        evaluate(_request(), policy, session_approved=False).decision == "allow"
    )


def test_path_within_project_rejects_escape(tmp_path: Any) -> None:
    """Test path within project rejects escape."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[Constraint(field="path", op="path_within_project")],
    )
    policy = Policy(always_allow=[rule])
    escape = evaluate(
        _request(args={"path": "/workspace/proj/../secret.txt"}),
        policy,
        session_approved=False,
    )
    assert escape.decision == "prompt"


def test_path_within_project_rejects_outside() -> None:
    """Test path within project rejects outside."""
    rule = AllowRule(
        spell=_identity(),
        constraints=[Constraint(field="path", op="path_within_project")],
    )
    policy = Policy(always_allow=[rule])
    outside = evaluate(
        _request(args={"path": "/etc/passwd"}), policy, session_approved=False
    )
    assert outside.decision == "prompt"


def test_command_prefix_matches_program_and_args() -> None:
    """Test command prefix matches program and args."""
    rule = AllowRule(
        spell=_identity("bash"),
        constraints=[
            Constraint(
                field="command",
                op="command_prefix",
                program_glob="git",
                args_glob="status*",
            )
        ],
    )
    policy = Policy(always_allow=[rule])
    assert (
        evaluate(
            _request(name="bash", args={"command": "git status"}),
            policy,
            session_approved=False,
        ).decision
        == "allow"
    )
    assert (
        evaluate(
            _request(name="bash", args={"command": "git push origin main"}),
            policy,
            session_approved=False,
        ).decision
        == "prompt"
    )


def test_command_prefix_rejects_forbidden_chars() -> None:
    """Test command prefix rejects forbidden chars."""
    rule = AllowRule(
        spell=_identity("bash"),
        constraints=[
            Constraint(field="command", op="command_prefix", program_glob="git")
        ],
    )
    policy = Policy(always_allow=[rule])
    for bad in [
        "git status; rm -rf /",
        "git status | cat",
        "git status && echo hi",
        "git $HOME",
        "git `whoami`",
        "git (sub)",
        "git {a,b}",
        "git < /etc/passwd",
        "git status\r\n",
    ]:
        outcome = evaluate(
            _request(name="bash", args={"command": bad}),
            policy,
            session_approved=False,
        )
        assert outcome.decision == "prompt", f"should reject {bad!r}"


def test_command_prefix_rejects_wrong_program() -> None:
    """Test command prefix rejects wrong program."""
    rule = AllowRule(
        spell=_identity("bash"),
        constraints=[
            Constraint(field="command", op="command_prefix", program_glob="git")
        ],
    )
    policy = Policy(always_allow=[rule])
    outcome = evaluate(
        _request(name="bash", args={"command": "rm -rf /tmp/x"}),
        policy,
        session_approved=False,
    )
    assert outcome.decision == "prompt"


def test_command_prefix_no_args_glob_allows_any_args() -> None:
    """Test command prefix no args glob allows any args."""
    rule = AllowRule(
        spell=_identity("bash"),
        constraints=[
            Constraint(field="command", op="command_prefix", program_glob="ls")
        ],
    )
    policy = Policy(always_allow=[rule])
    outcome = evaluate(
        _request(name="bash", args={"command": "ls -la /tmp"}),
        policy,
        session_approved=False,
    )
    assert outcome.decision == "allow"


# -- schemaless precedence ----------------------------------------------------


def _schemaless_request(**kwargs: Any) -> ApprovalRequest:
    """Build a schemaless request (no parameter schema on the spell)."""
    request = _request(**kwargs)
    return ApprovalRequest(
        cast_id=request.cast_id,
        spell=request.spell,
        arguments=request.arguments,
        argument_digest=request.argument_digest,
        project_root=request.project_root,
        tome_id=request.tome_id,
        session_id=request.session_id,
        is_read_only=request.is_read_only,
        runner_origin=request.runner_origin,
        schemaless=True,
    )


def test_session_approval_does_not_match_schemaless() -> None:
    """Test session approval does not match schemaless casts."""
    policy = Policy()
    outcome = evaluate(_schemaless_request(), policy, session_approved=True)
    assert outcome.decision == "prompt"


def test_project_approval_does_not_match_schemaless() -> None:
    """Test project approval does not match schemaless casts."""
    policy = Policy(approved_projects=[ProjectApproval(root="/workspace/proj")])
    outcome = evaluate(_schemaless_request(), policy, session_approved=False)
    assert outcome.decision == "prompt"


def test_deny_still_beats_schemaless() -> None:
    """Test explicit deny rules still match schemaless casts."""
    policy = Policy(always_deny=[DenyRule(spell=_identity())])
    outcome = evaluate(_schemaless_request(), policy, session_approved=True)
    assert outcome.decision == "deny"
    assert outcome.reason_code == "rule"


def test_read_only_auto_allow_still_matches_schemaless() -> None:
    """Test read-only auto-allow still covers schemaless casts."""
    policy = Policy()
    outcome = evaluate(
        _schemaless_request(read_only=True), policy, session_approved=False
    )
    assert outcome.decision == "allow"
    assert outcome.reason_code == "read_only"
