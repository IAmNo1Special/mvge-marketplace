# Quickstart

From nothing to a Mvge running a real task. Budget five minutes; on a warm
network cache the install steps take about forty seconds.

Every install command below was run end to end on a clean machine — an empty
`$HOME`, empty `XDG` directories, a private `uv` cache, and nothing preinstalled
on `PATH` — before being written here, with only the step-0 key exported. Each is
re-run on a clean machine on a schedule, because a documented command that stops
working is worse than no documentation at all. The model call in step 4 is a
different matter and is not covered by that claim; see
[What is not verified yet](#what-is-not-verified-yet).

## What this page promises, exactly

**Your first run needs no account, but it does need a free OpenRouter key.**
Install a Realm, install a Mvge, run a task. There is a credential step — step 0
— because as of 2026-10-11 no free tier answers an unauthenticated request: the
OpenCode Realm closed its free tier to callers outside OpenCode, and OpenRouter's
`:free` models have always required a key (ADR-0020 in the engine repo).

The model is still **free**. The key identifies you to a provider that has
stopped serving anonymous callers; it does not introduce a cost.

**It does not promise the model call always answers, and no version of MvgeOS
can.** The default Realm's free tier is not MvgeOS's to give you, and it is
shared with everyone else on it. Steps 1-3 are your side of the line and they
work; step 4 is somebody else's, and when it says no, that is not your setup.

## Checking your version

There is no `mvgeos --version` yet — the flag does not exist, so this is how you
check what you have:

```console
$ uv tool list | grep mvgeos-cli
```

If a command on this page is missing when you run it, you have something older
than `v0.6.8`. The newest command here, `mvge install --confirm-python-deps` in
step 3, landed in `v0.6.7` — it is not in `v0.6.6`.

This page deliberately does not name a version. It used to, and the number was
wrong for six releases running, because it named the engine's newest source
commit while the install line above installs the newest *published* wheel — and
those two are only the same number in the moment a release is cut. Stating a
number that cannot be kept true is worse than stating none, so the number is
gone and what replaces it is the thing a reader actually needs: the commands
below and the oldest release that has them.

This page is still not version-pinned, and the reason is unchanged: the install
is not pinned either, so there are no versions to choose between. Once the
install becomes a released package, this page is what gets versioned per
release, and the version selector appears here.

!!! warning "Steps 1-3 always work. Step 4 depends on someone else's free tier."
    The install is yours and takes seconds. The model call in step 4 goes through
    OpenCode Zen's free tier, which is shared and can be at capacity. When it is,
    step 4 fails with a rate-limit message and it is not your setup. That is the
    most common reason this page appears broken on day one, and
    [Troubleshooting](troubleshooting.md#rate-limited-by-the-provider)
    has the message your version prints for it. Everything below step 4 still
    works.

## Before you start

You need two things:

| Requirement | Why |
| --- | --- |
| [`uv`](https://docs.astral.sh/uv/) | MvgeOS installs and runs entirely through `uv` / `uvx`. There is no `pip` path. |
| Python 3.13+ | Pulled in automatically by `uv`. You do not need to install it yourself. |

You do not need an account, a Python environment, a virtualenv, or a clone of
this repository. You do need a free OpenRouter API key — see step 0.

## 0. Set your free key

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

Get a key at [openrouter.ai/keys](https://openrouter.ai/keys); the free keys
cost nothing to create and the default model this page uses is billed at $0. This
step is new since 2026-10-11: the engine used to run with no credential at all,
because OpenCode's free tier served anonymous callers, and that ended. If you
already have an OpenCode key you would rather use, see [Use another
Realm](#use-another-realm) — but the default model is on OpenRouter, so the
Realm below and this key are what step 4 needs.

## 1. Install the CLI

Pick one. The first is for trying it; the second gives you a persistent
`mvgeos` command.

```bash
# Try it without installing anything -- runs from a throwaway environment
uvx mvgeos --help
```

```bash
# Or install it once and get a real `mvgeos` command on your PATH
uv tool install mvgeos
mvgeos --help
```

Both spellings print the command table and exit 0:

```text
 Usage: mvgeos [OPTIONS] COMMAND [ARGS]...

 MvgeOS - a Python-based AI coding agent

╭─ Commands ────────────────────────────────────────────────────────────────────╮
│ build   Serialise the resolved runtime manifest (read-only).                 │
│ config  Configuration management                                             │
│ info    Display the runtime snapshot as rich tables (read-only).             │
│ mvge    Mvge (agent) management                                              │
│ rune    Extension rune management                                            │
│ setup   Install system dependencies for runes                                │
│ tome    Session tome management                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Every command on the rest of this page works with either spelling: write `uvx
mvgeos …` if you did not install, `mvgeos …` if you did. `uvx` adds a few
seconds per call because it re-resolves the environment; `uv tool install` does
not.

!!! note "Running from source instead"
    Cloning the monorepo and running `uv sync && uv run mvgeos` also works, and
    is what contributors want. It pulls in the desktop GUI's dependencies too,
    so it is noticeably heavier than the two commands above.

## 2. Give it access to a model

MvgeOS reaches models through a **Realm** — an extension that speaks one
provider's API. No Realm is installed yet, and the CLI will not talk to a model
without one.

```bash
uvx mvgeos rune install openrouter-realm --confirm-python-deps
```

```text
Successfully installed rune 'openrouter-realm' to
~/.agents/extensions/openrouter-realm
```

**`openrouter-realm` is the Realm you want, and the name matters.** The engine's
default model is `openrouter/free`; the slug's prefix is the Realm, so a run
resolves the `openrouter` Realm and nothing else answers it. Installing a
different Realm gets you a working engine that cannot reach its own default
model. Reproduced on a clean `$HOME` with the wrong install exiting 0 and the
task then failing:

```text
$ uvx mvgeos --agent-name coding_mvge "Create a file named hello.txt containing exactly the text: hello from mvgeos"
Error: No Realm factory registered for model 'openrouter/free'. Run
'mvgeos rune install openrouter-realm' to install it from the central marketplace.
```

Current builds name the Rune to install in that message. Older builds named only
`openrouter-realm`, which is worse than no remedy — so if you followed an older
page and installed that one, install this one too.

OpenRouter is a fine Realm and is in the catalog — see
[Use another Realm](#use-another-realm) — it is just not the one the default
model needs.

`--confirm-python-deps` acknowledges that the Rune's manifest is an instruction
to fetch packages from PyPI. Read the manifest before you pass it if you would
rather not. See [OpenRouter Realm](runes/openrouter-realm.md) for what it
registers.

## 3. Install a Mvge

An engine with no Mvge has no Spells. `coding_mvge` is the coding Mvge — it
ships nine built-in Spells: `bash`, `edit`, `find`, `grep`, `list_files`,
`read`, `read_url`, `search_web`, `write`.

```bash
uvx mvgeos mvge install coding_mvge
```

That is the whole step. It takes no flag, and on a terminal it may ask one
question you do not have to care about.

!!! note "You may see a prompt, and it is safe to skip"
    `coding_mvge` declares four `python_deps`, and installing a Mvge gates them
    the same way `rune install` does. On a TTY you get a `[y/N]` prompt.

    **The four packages it names — `mvgeos-agent`, `mvgeos-core`,
    `mvgeos-provider`, `mvgeos-runes` — are already installed as dependencies of
    the engine you installed in step 1.** So answering `n`, or skipping
    automatically in a script, costs you nothing here. Measured on a clean
    machine, the resolved Spell set is the same nine either way:

    ```text
    with    --confirm-python-deps -> 9  bash edit find grep list_files read read_url search_web write
    without --confirm-python-deps -> 9  bash edit find grep list_files read read_url search_web write
    ```

    Do not read the skip warning as a broken install. It reads like an error and
    is not one.

    To fetch them anyway, `mvge install` takes the same flag `rune install` does,
    added in `v0.6.7`:

    ```bash
    uvx mvgeos mvge install --confirm-python-deps coding_mvge
    ```

    This matters for a Mvge whose dependencies are **not** already covered by
    the engine. `coding_mvge`'s are, so the flag is optional here.

```text
Non-interactive session: skipping install of unreviewed python dependencies
['mvgeos-agent', 'mvgeos-core', 'mvgeos-provider', 'mvgeos-runes'].
Re-run with confirm=True to install.
Successfully installed mvge 'coding_mvge' to ~/.agents/agents/coding_mvge
```

## 4. Run one task

There is no authentication step, and skipping one is not an oversight. The
default model is a free model on a Realm whose free tier answers with no
credential at all, so the first task runs as the first command you type. If you
want a Realm that needs a key instead, that is
[Use another Realm](#use-another-realm).

```bash
uvx mvgeos --agent-name coding_mvge "Create a file named hello.txt containing exactly the text: hello from mvgeos"
```

This is real output, pasted unedited from a run on a clean machine. The only
change is that the terminal's own line wrapping is preserved rather than
reflowed:

```text
Created hello.txt containing exactly hello from mvgeos — verified byte-for-byte
with od -c (17 bytes, no trailing newline).
Stop reason: stop
```

The file is really there, and the content is byte-for-byte what was asked for:

```console
$ ls -l hello.txt
-rw-r--r-- 1 you you 17 hello.txt
$ od -c hello.txt
0000000   h   e   l   l   o       f   r   o   m       m   v   g   e   o
0000020   s
0000021
$ wc -c hello.txt
17 hello.txt
```

17 bytes for 17 characters: there is no trailing newline, because the Mvge was
asked for *exactly* that text and did precisely that.

**And no credential was written, which is the claim worth checking rather than
believing.** After that run, the only directories under `~/.agents/` are the
three this page told you to create:

```console
$ ls ~/.agents/
agents  extensions  sessions
```

There is no `auth/`. Nothing asked you for a key, because nothing needed one.

!!! warning "The default free model is the fragile part of this page"
    Every install and inspect command above was verified on a clean machine and
    works. This model call is not, because the free tier is not MvgeOS's to give
    you. When it is at capacity the message is:

    ```text
    Rate limited by the provider: Realm requested 2209s retry delay (max: 60s). Rate
    limit exceeded. Please try again later..
    No spells ran before the failure, so nothing was written.
    ```

    Measured today, not guessed: on a clean `$HOME`, this exact command was
    refused seven times and then succeeded, once the window it names had passed.
    The window it asked for was about 35 minutes, and the run that succeeded
    started roughly 40 minutes after the first refusal — so a fast retry loop
    does not clear it, and reading the number does. A refused attempt costs a few
    seconds and writes nothing.

    Check the file before you re-run, so you do not overwrite work you already
    have:

    ```bash
    ls -l hello.txt && cat hello.txt || uvx mvgeos --agent-name coding_mvge \
      "Create a file named hello.txt containing exactly the text: hello from mvgeos"
    ```

    The check comes first on purpose. A run that dies *after* a `write` can leave
    a correct file behind and still exit non-zero.

    The full message set, including what a spent OpenRouter daily allowance looks
    like when you have switched Realms, is in
    [Troubleshooting](troubleshooting.md).

### What actually happened underneath

The Mvge verified its own work rather than assuming it, which is the behaviour
you want. Here is the run above as recorded in its Tome, unedited:

```text
think  I'll create that file.
cast   write({"path": "hello.txt", "content": "hello from mvgeos"})
result Wrote to hello.txt
think  Let me verify the file content. The user asked for "exactly the text: hello from
       mvgeos". I wrote it without a trailing newline. Let me verify.
cast   bash({"command": "cat -A hello.txt; echo \"---\"; wc -c hello.txt"})
result Invalid arguments for spell bash: 3 validation errors for bash_Schema
       cwd
         Input should be a valid string
       timeout_ms
         Input should be a valid integer
       workspace_root
         Input should be a valid string
cast   bash({"command": "cat -A hello.txt; ...", "cwd": "<workdir>",
            "timeout_ms": 10000, "workspace_root": "<workdir>"})
result hello from mvgeos---
       17 hello.txt
say    Created `<workdir>/hello.txt` containing `hello from mvgeos` (17 bytes, no
       trailing newline).
```

Note the first `bash` cast. The Mvge passed only the required argument, exactly
as the Spell's own signature allows, and it was rejected for three arguments it
never sent. This is a real engine bug, not a mistake in the prompt: a Spell
argument declared optional loses its nullability when the JSON Schema is turned
into a validator, and because a Spell is validated twice per cast, the first
pass fills the omitted arguments with `null` and the second pass then rejects
them.

It is tracked as SOM-23. It is harmless to correctness — the task completed and
the file was right — but it costs one extra model round-trip per self-check, so
expect a slightly higher Mana bill than the transcript implies.

### Replay it yourself

Every run is recorded. To see what the Mvge cast and concluded:

```bash
uvx mvgeos tome list
uvx mvgeos tome show <id>
```

## 5. Check what you actually have

Worth running once. It resolves the whole runtime the same way a real run does
and prints it: which Spells are loaded, which Runes are enabled and which
Sigils they hooked, and where every config value came from.

```bash
uvx mvgeos info --agent-name coding_mvge
```

```text
Agent: coding_mvge
Model: openrouter/free
```

The `Model:` line is the Realm-prefixed slug this Mvge resolves to, and its
prefix is the Realm the run will use — which is why step 2 asked for
`openrouter-realm`. Below it the real table lists the nine Spells by name with a
Description column several paragraphs long each, and is followed by the Rune,
Config, Prompt, Skills, and Diagnostics tables. All elided here for width. For
the full parameter schemas as JSON:

```bash
uvx mvgeos build --agent-name coding_mvge
```

## Use another Realm

The default Realm is the one the key in step 0 is for. If you already pay for
something else, that is the supported path and it is the reason the Realm is an
abstraction. Install the Realm you want, give it its key, and name a model its
slug space:

```bash
uvx mvgeos rune install openrouter-realm --confirm-python-deps
export OPENROUTER_API_KEY="sk-or-..."
uvx mvgeos --agent-name coding_mvge -m nvidia/nemotron-3-super-120b-a12b:free \
  "Create a file named hello.txt containing exactly the text: hello from mvgeos"
```

Each Realm reads its own variable — `OPENROUTER_API_KEY` for OpenRouter,
`OPENCODE_API_KEY` for Zen — and the CLI does not fall back to another Realm's
variable, because a key presented to the wrong host authenticates nowhere and
then fails with a message about the model rather than about the credential. A
key reaches the Realm you named and nothing else.

Two Realms ship today:

| Realm | Credential | What it is |
| --- | --- | --- |
| [openrouter-realm](runes/openrouter-realm.md) | `OPENROUTER_API_KEY` | OpenRouter. The default. One key, a wide catalog. Free models carry a `:free` suffix. |
| [opencode-realm](runes/opencode-realm.md) | `OPENCODE_API_KEY` | OpenCode Zen. Free models carry a `-free` suffix and no longer answer callers outside OpenCode (ADR-0020). |

`-m` accepts only ids from the model list shipped in the repository, and that
list is a drifting snapshot — see
[Troubleshooting](troubleshooting.md#error-unknown-model). Run
`mvgeos info --agent-name coding_mvge` to see the slug you would otherwise get,
and read the shipped list before you name a different one.

!!! warning "Scripting step 3 needs the flag, not a piped `y`"
    `mvgeos mvge install` takes `--confirm-python-deps`, the same flag
    `rune install` takes — see step 3. Use it in a script or CI job, where stdin
    is not a TTY and nothing is prompted:

    ```bash
    mvgeos mvge install --confirm-python-deps coding_mvge
    ```

    Without the flag, a non-interactive session skips the fetch and says so
    rather than failing:

    ```text
    Non-interactive session: skipping install of unreviewed python dependencies
    ['mvgeos-agent', 'mvgeos-core', 'mvgeos-provider', 'mvgeos-runes'].
    ```

    For `coding_mvge` that skip is harmless — the four packages are already
    installed as dependencies of the engine, and the resolved Spell set is the
    same nine either way. For a Mvge whose `python_deps` are *not* already
    covered by the engine, it is the difference between a working Mvge and a
    Mvge missing its dependencies. Read the output either way; do not assume.

!!! warning "Put `--agent-name` after the subcommand"
    `mvgeos --agent-name coding_mvge info` silently ignores the flag and reports
    `Agent: default-mvge` with no Spells loaded — no error, just the wrong Mvge.
    `mvgeos info --agent-name coding_mvge` is correct. For the one-shot run in
    step 4 there is no subcommand, so the flag goes before the prompt.

## Where things landed

Everything installed into the standard `.agents` directories:

```text
~/.agents/
├── extensions/openrouter-realm/   # the Realm Rune
├── agents/coding_mvge/          # the Mvge, and its Spells
└── sessions/                    # Tomes, as JSONL
```

There is no `auth/` entry — step 0 put the key in your environment rather than
on disk. Export one per Realm you use.

## Interactive use

The one-shot form above is for scripting. For a conversation, run `mvgeos` with
no prompt:

```bash
uvx mvgeos --agent-name coding_mvge
```

Add `--tui` for the full-screen terminal interface. Spell casts that mutate
anything can require your approval; the REPL will prompt, and
`--approval-mode allow-all` skips the gate for a single run if you trust the
Tome.

## What is not verified yet

Stated plainly, because this page is meant to be trustworthy.

**Verified end to end on a clean machine, unedited:** every command on this page,
including the model call in step 4. Empty `$HOME`, empty `XDG` directories,
private `uv` cache, only the step-0 key exported, stdin not a TTY. The run
that succeeded is the one pasted above.

The model call was not reliable on the day it was checked, and that is not MvgeOS
to fix. What remains true:

- **Step 4 can be refused, and the message names the cause.** Measured on that
  clean `$HOME`, this exact command was refused seven times and then succeeded,
  once the window it names had passed. The refusal:

    ```text
    Rate limited by the provider: Realm requested 2209s retry delay (max: 60s). Rate
    limit exceeded. Please try again later..
    No spells ran before the failure, so nothing was written.
    ```

    The delay it asks for is around 35 minutes, and the successful run started
    about 40 minutes after the first refusal. A tight retry loop does not clear
    it — nothing is wrong, you are early.

- **A refusal writes nothing.** The message above ends with
    `No spells ran before the failure, so nothing was written.`, and the check
    confirmed an empty working directory. But a run that dies *after* a `write`
    can leave a correct file behind and still exit non-zero, so check before you
    re-run rather than assuming:

    ```bash
    ls -l hello.txt && cat hello.txt || uvx mvgeos --agent-name coding_mvge \
      "Create a file named hello.txt containing exactly the text: hello from mvgeos"
    ```

    The check comes first on purpose, so a re-run cannot overwrite work you
    already have.

- **Install timings are from a warm cache.** About 1s to resolve and install with
  `uv tool install`, and a few seconds each for the two `rune`/`mvge` installs.
  A genuinely cold machine is slower; `uvx` re-resolves on every call, so the
  `uvx` spelling costs a few seconds more per command than the installed one.

- **Model choice is narrower than either Realm's live catalog.** `-m` resolves
  against a static list shipped in the repository, that list is a drifting
  snapshot, and no `mvgeos` subcommand refreshes it. Real failures are documented
  under [`Error: Unknown model`](troubleshooting.md#error-unknown-model).

- The Spell bug described in step 4 is open (SOM-23). Until it is fixed, expect
  one extra model round-trip whenever a Mvge checks its own work. It is not
  limited to `read` — the run pasted above hit it on `bash`.

- MvgeOS is pre-1.0. The command surface moves, and the version moves faster
  than this page does — see
  [Which version this page describes](#which-version-this-page-describes).

- Rune code runs **in-process** via `importlib`. A Rune is Python that executes
  inside the engine with your permissions. Read manifests before installing
  them. See [approval-rune](runes/approval-rune.md) for the gate that puts a
  human back in front of mutating casts.

Next: [Concepts](concepts.md) for what the vocabulary means,
[Troubleshooting](troubleshooting.md) if something broke,
or the [Rune catalog](index.md#rune-catalog) to see what else exists.