# Whole-process wrapping, Phase 5b (opt-in deployment posture)

Phase 5b wraps a Phase 4 host process in an OS/container
boundary (bwrap on Linux, Docker where available), with
workspace-only mounts, a scrubbed environment, and an
all-or-none network policy. It is NOT the default, NOT a
release gate, and NOT a manifest field: no rune declares
wrapping, and wrapping changes nothing about which runes the
engine hosts.

Honest claim, do not upgrade it: **on a wrapped deployment
only, untrusted runes run inside an OS/container boundary
with a stated, tested policy.** Unwrapped, the Phase 4 claim
stands. Pinned means unmodified, not benign, wrapped or not.

## What wrapping does not change

Wrapping wraps the host command only. Classification is
unchanged: the in-process-only tier in
`docs/runes/hosting-v1.md` (approval-rune, realm runes,
spell-gateway runes, codec runes, spell-modifier runes, and
any rune declaring `hosting: "in-process"`) stays
in-process and pinned, and wrapping validation refuses such
a manifest outright rather than claiming confinement for it.
Eligible runes stay eligible; wrapping does not promote an
eligible rune to verified-hosted, and it does not change any
rune's capabilities, write roots, or pin. The engine-side
capability gate still applies to every wire registration
from a wrapped host.

## Per-platform enforcement matrix (summary)

The full matrix, mount table, and proof procedure live in the
engine doc
(`docs/agents/rune-whole-process-wrapping-5b.md` in the
`mvge` repository); this table is the marketplace-facing
summary and must not drift from it:

| Platform / backend | Enforcement | Gap |
| --- | --- | --- |
| Linux / `bwrap` | full | None claimed beyond the stated mounts; `/proc` is a fresh procfs for the new PID namespace, so host PIDs and host process environ are not visible inside the wrap. |
| Linux, macOS, Windows/WSL2 / `docker` (Linux containers) | full | Requires a Docker daemon; the image must provide `python3` and `mvge`. |
| macOS without Docker | unavailable | Seatbelt is a Phase 5a write policy, not wrapping; wrapping refuses. |
| Windows without Docker | write-confined only | Read/network documented gap (Phase 5a ACL ladder alone). Do not claim full confinement on Windows without Docker. |
| Windows containers | untested, not claimed | Do not present as proven. |

Network in v1 is all-or-none on every platform: denied
means an unshared network namespace or `--network none`;
allowed means the host network or the default bridge network.
There is no per-destination egress allowlist in v1.

## Deployment proof

A deployment is claimed confined only after the engine proof
script passes against that deployment's own workspace,
backend, and mounts:

```sh
uv run python scripts/verify_wrapped_deployment.py \
  --workspace /path/to/workspace \
  --backend bwrap \
  --write-root data
```

The script checks, inside the wrapped argv, that a secret
outside the mounts cannot be read or written, that the
workspace root cannot be written unless declared, that
declared roots can be written, that no credential-shaped
environment variable is visible, that the network is denied
when denied (proved against a listener the script controls,
not a dead port), that host `/proc` entries are not visible,
and that no credential-named file exists under any mount.
It exits non-zero on any failure. A wrapped
deployment whose proof has not been run (or has failed) must
not be described as confined.

## Still deferred

The approved one-time escalation deferred from Phase 5a (F3)
is still deferred: widening a wrapped deployment means
constructing a new, wider policy, an explicit operator act,
not a runtime widening. No `escalate()` path is built here.
