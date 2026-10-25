"""Tests for policy loading, malformed-TOML semantics, install-id binding."""

import stat
from pathlib import Path

from mvge_runes_approval_rune.contracts import ApprovalRequest, SpellIdentity
from mvge_runes_approval_rune.normalization import digest_arguments
from mvge_runes_approval_rune.policy import (
    AllowRule,
    Policy,
    PolicyStore,
    evaluate,
    load_policy,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_valid_policy(tmp_path: Path) -> None:
    """Test load valid policy."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1
install_id = "id-1"

[[always_deny]]
[always_deny.spell]
name = "deploy"
source_kind = "builtin"
source_id = "ops"
source_scope = "agent"

[[always_allow]]
[always_allow.spell]
name = "write"
source_kind = "builtin"
source_id = "coding_mvge"
source_scope = "agent"
code_digest = "sha256:abc"

[[approved_projects]]
root = "/workspace/proj"
enabled = true
""",
    )
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert policy.schema_version == 1
    assert len(policy.always_deny) == 1
    assert len(policy.always_allow) == 1
    assert len(policy.approved_projects) == 1
    assert policy.approved_projects[0].root == "/workspace/proj"
    assert not policy.broken
    assert policy.warnings == []


def test_load_missing_file_yields_empty_policy(tmp_path: Path) -> None:
    """Test load missing file yields empty policy."""
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert policy.always_deny == []
    assert policy.always_allow == []
    assert policy.approved_projects == []
    assert not policy.broken


def test_recoverable_malformed_rule_dropped_with_warning(
    tmp_path: Path,
) -> None:
    """Test recoverable malformed rule dropped with warning."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1
install_id = "id-1"

[[always_allow]]
[always_allow.spell]
name = "write"
# missing source_kind/source_id/source_scope/code_digest -> invalid rule

[[always_allow]]
[always_allow.spell]
name = "read"
source_kind = "builtin"
source_id = "coding_mvge"
source_scope = "agent"
code_digest = "sha256:ok"
""",
    )
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert not policy.broken
    assert len(policy.always_allow) == 1
    assert policy.always_allow[0].spell.name == "read"
    assert any(
        "always_allow[0]" in w or "dropped" in w for w in policy.warnings
    )


def test_unrecoverable_toml_yields_broken_policy(tmp_path: Path) -> None:
    """Test unrecoverable toml yields broken policy."""
    policy_path = tmp_path / "policy.toml"
    _write(policy_path, "schema_version = [[[[ not toml\n")
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert policy.broken
    assert policy.always_allow == []
    assert policy.always_deny == []
    assert any(
        "blocking" in w.lower() or "unrecoverable" in w.lower()
        for w in policy.warnings
    )


def test_install_id_mismatch_ignores_entire_policy(tmp_path: Path) -> None:
    """Test install id mismatch ignores entire policy."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1
install_id = "old-install"

[[always_deny]]
[always_deny.spell]
name = "deploy"
source_kind = "builtin"
source_id = "ops"
source_scope = "agent"
""",
    )
    store = PolicyStore(tmp_path, install_id="new-install")
    policy = load_policy(store)
    # Mismatch: the whole policy is ignored, including deny rules.
    assert policy.always_deny == []
    assert policy.always_allow == []
    assert policy.install_mismatch
    assert any("install" in w.lower() for w in policy.warnings)


def test_install_id_missing_stamped_once_on_migration(tmp_path: Path) -> None:
    """Test install id missing stamped once on migration."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1

[[always_deny]]
[always_deny.spell]
name = "deploy"
source_kind = "builtin"
source_id = "ops"
source_scope = "agent"
""",
    )
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert len(policy.always_deny) == 1
    assert store.read_install_id() == "id-1"


def test_policy_ignored_when_install_id_unavailable(tmp_path: Path) -> None:
    """Test policy ignored when install id unavailable."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1

[[always_deny]]
[always_deny.spell]
name = "deploy"
source_kind = "builtin"
source_id = "ops"
source_scope = "agent"
""",
    )
    store = PolicyStore(tmp_path, install_id=None)
    policy = load_policy(store)
    assert policy.always_deny == []
    assert any("install-id" in w.lower() for w in policy.warnings)


def test_save_stamps_install_id_and_sets_user_only_perms(
    tmp_path: Path,
) -> None:
    """Test save stamps install id and sets user only perms."""
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = Policy(schema_version=1)
    store.save(policy)
    policy_path = tmp_path / "policy.toml"
    assert policy_path.exists()
    mode = stat.S_IMODE(policy_path.stat().st_mode)
    assert mode == 0o600
    dir_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    assert dir_mode == 0o700
    assert store.read_install_id() == "id-1"


def test_save_is_atomic_no_partial_file(tmp_path: Path) -> None:
    """Test save is atomic no partial file."""
    store = PolicyStore(tmp_path, install_id="id-1")
    store.save(Policy(schema_version=1))
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    policy = load_policy(store)
    assert policy.schema_version == 1


def test_round_trip_policy_with_constraints(tmp_path: Path) -> None:
    """Test round trip policy with constraints."""
    policy_path = tmp_path / "policy.toml"
    _write(
        policy_path,
        """schema_version = 1
install_id = "id-1"

[[always_allow]]
project = "/workspace/proj"
[always_allow.spell]
name = "bash"
source_kind = "builtin"
source_id = "coding_mvge"
source_scope = "agent"
code_digest = "sha256:abc"

[[always_allow.constraints]]
field = "command"
op = "command_prefix"
program_glob = "git"
args_glob = "status*"
""",
    )
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert len(policy.always_allow) == 1
    rule = policy.always_allow[0]
    assert rule.project == "/workspace/proj"
    assert len(rule.constraints) == 1
    assert rule.constraints[0].op == "command_prefix"
    store.save(policy)
    reloaded = load_policy(store)
    assert len(reloaded.always_allow) == 1
    assert reloaded.always_allow[0].constraints[0].program_glob == "git"


def test_unsupported_schema_version_warns_but_loads(tmp_path: Path) -> None:
    """Test unsupported schema version warns but loads."""
    policy_path = tmp_path / "policy.toml"
    _write(policy_path, 'schema_version = 99\ninstall_id = "id-1"\n')
    store = PolicyStore(tmp_path, install_id="id-1")
    policy = load_policy(store)
    assert any("schema_version" in w for w in policy.warnings)


def test_engine_stamped_rule_round_trips_and_matches(tmp_path: Path) -> None:
    """Test engine stamped allow rule round trips and matches.

    Regression: the gate persists allow rules with the engine-derived
    five-field identity. The loader must accept them back, and a code
    digest change must stop matching (grant invalidation).
    """
    identity = SpellIdentity(
        name="write",
        source_kind="builtin",
        source_id="tests.WriteSpell",
        source_scope="agent",
        code_digest="sha256:aaa",
    )
    store = PolicyStore(tmp_path, install_id="id-1")
    store.save(Policy(always_allow=[AllowRule(spell=identity, id="allow-0")]))
    reloaded = load_policy(store)
    assert len(reloaded.always_allow) == 1
    rule = reloaded.always_allow[0]
    assert rule.spell.full_key() == identity.full_key()

    arguments = {"path": "/workspace/proj/a.txt"}

    def _request(digest: str) -> ApprovalRequest:
        return ApprovalRequest(
            cast_id="call_1",
            spell=SpellIdentity(
                name="write",
                source_kind="builtin",
                source_id="tests.WriteSpell",
                source_scope="agent",
                code_digest=digest,
            ),
            arguments=arguments,
            argument_digest=digest_arguments(arguments),
            project_root="/workspace/proj",
            tome_id="tome-1",
            session_id="sess-1",
            is_read_only=False,
            runner_origin=False,
            schemaless=False,
        )

    outcome = evaluate(_request("sha256:aaa"), reloaded, session_approved=False)
    assert outcome.decision == "allow"
    assert outcome.rule_id == "allow-0"

    # A code change (different digest) invalidates the grant: prompt again.
    changed = evaluate(_request("sha256:bbb"), reloaded, session_approved=False)
    assert changed.decision == "prompt"
