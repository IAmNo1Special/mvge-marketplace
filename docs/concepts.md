# Concepts

MvgeOS has a strict domain vocabulary. It is used exactly, in the code, the
docs, and the CLI. This page exists because the words do not map cleanly onto
the generic ones — "agent", "tool", and "provider" all mean something more
specific here.

## The vocabulary

| You would normally say | MvgeOS says | What it is |
| --- | --- | --- |
| Agent | **Mvge** | The autonomous entity that acts for a Summoner. Pronounced "mage". |
| User | **Summoner** | The human the Mvge acts for. |
| Tool | **Spell** | An executable capability a Mvge can cast during a run. |
| Token | **Mana** | The unit model usage is measured and budgeted in. |
| Context window | **Mana Pool** | How much Mana fits in one request. |
| Provider | **Realm** | An abstraction over a service that serves models. |
| Session | **Tome** | A persisted conversation, stored as append-only JSONL. |
| Message | **Invocation** | One entry in a Tome: a request, a response, or a Spell result. |
| Streaming | **Channeling** | Receiving a response incrementally from a Realm. |
| Extension | **Rune** | A packaged extension that hooks a Mvge's lifecycle. |
| Hook | **Sigil** | A lifecycle point a Rune registers a callback on. |
| Reasoning effort | **Contemplation** | How much the Mvge thinks per request. |

The full definitions, including what each term is *not*, live in the
domain glossary. This table is the working subset.

## Mvge, Spell, Realm

The three that matter on day one.

A **Mvge** is not the engine. The engine is `mvgeos-agent` plus `mvgeos-core`.
A Mvge is a configured instance of it: a name, a model, a set of enabled
Spells, a system prompt. `coding_mvge` is the one shipped in the marketplace,
and it carries the nine built-in development Spells — `bash`, `edit`, `find`,
`grep`, `list_files`, `read`, `read_url`, `search_web`, `write`.

A **Spell** is a tool the Mvge casts. Spells come from three places:

- **built in** to the Mvge's package, discovered from its `spells/` directory
- a **Rune** that registers them
- the **Seeker**, which discovers Spells and Skills on demand at runtime

A **Realm** is how a model gets reached. The engine does not talk to any
provider's API directly; it talks to a Realm, and Realms are Runes.
`openrouter-realm` is the default, because the engine's default model
(`openrouter/free`) names it in its own prefix. A Realm can be a router that fans out to many upstream providers,
or a direct provider where the Realm and the provider are the same organisation.

The prefix in a model slug is the Realm, and it is not decoration — it is what
decides which Rune has to be installed. `-m openrouter/free` served by `openrouter-realm`
and `-m opencode/…` served by `opencode-realm` are different requirements, and
the wrong one fails with a message about the missing Realm. Two Realms ship today: [openrouter-realm](runes/openrouter-realm.md) and
[opencode-realm](runes/opencode-realm.md).

!!! note "Realm vs. Provider"
    These are deliberately different words. The **Realm** is the abstraction —
    the interface the engine programs against. The **provider** is the
    organisation behind a specific model, derived from the model-ID prefix:
    `nvidia/nemotron-...` means provider `nvidia`. Never store the provider as a
    field; it is computed from the ID.

## Tome, Invocation, Leaf, Fork

Every conversation is a **Tome**: an append-only JSONL file under
`~/.agents/sessions/`. Not a chat log in a database — a file you can read,
diff, and version.

Each entry is an **Invocation**: a Summoner's request, a Mvge's response, or a
**SpellResultMessage** from a cast Spell. Invocations are recorded at exactly
one point in the loop, so the transcript is complete without special cases.

The **Leaf** is the position marker in a Tome — an explicit entry saying which
point you are at. **Fork** creates a new Tome branching from a Leaf; the child
records a reference to its parent in its header and copies the ancestor path.
Tomes therefore form a tree, and you can take an old conversation in a new
direction without destroying it.

```bash
mvgeos tome list
mvgeos tome show <id>
mvgeos tome fork <id>
mvgeos tome export <id> -f markdown
```

!!! note "Tome on disk, Session on the wire"
    At filesystem, wire, and CLI boundaries MvgeOS speaks the open
    `.agents` protocol, where the standard term is **session** — so
    `type: "session"` on the wire and `~/.agents/sessions/` on disk. `Tome` is
    the domain term: the Python model, the persona, and `mvgeos tome`. This is
    deliberate, so MvgeOS files interoperate with the protocol rather than
    inventing a private layout.

## Rune and Sigil

A **Rune** is an extension. It hooks lifecycle points — **Sigils** — and can
register Spells, CLI commands, shortcuts, and Realms.

```text
~/.agents/extensions/<rune-name>/
├── manifest.json    # name, version, entry point, Sigils, Spells, commands
├── rune.py          # entry point exposing rune_factory
└── tests/
```

A Rune declares which Sigils it handles in its manifest, and the engine calls
it at the right moment. A Rune that injects repository context on every run
subscribes to the session-start Sigil; one that reacts to Spell results
subscribes to the after-result Sigil. Runes can also mount CLI commands, which
appear in `mvgeos --help` as though they were built in — resolved through the
same discovery path that loads the Rune, so a command that shows up is a Rune
that would load.

!!! warning "Runes run in-process"
    Rune code is imported and executed **inside the engine** via `importlib`.
    It is not sandboxed and it is not process-isolated — a Rune has your
    permissions. Read the manifest, since `python_deps` is an instruction to
    fetch packages from PyPI. There is an opt-in sandbox seam for code a Rune
    voluntarily submits for sandboxed execution, and
    [approval-rune](runes/approval-rune.md) is a fail-closed gate that puts a
    human back in front of mutating casts. Neither is on by default.

### What a Rune may import

A Rune may import engine packages: `mvgeos_core`, `mvgeos_runes`, `mvgeos_agent`,
`mvgeos_provider`, `mvgeos_tome`. It may never import another Rune. That absolute
boundary is what makes each Rune independently installable on its own — engine
packages are supplied by the host, siblings are not, and a Rune that reaches for a
sibling does not work once it is installed alone.

Which *modules* an engine publishes to a Rune is enumerated in
`mvgeos_runes.contract.RUNE_IMPORTABLE_MODULES`, and
`tests/test_engine_import_contract.py` asserts nothing outside that set is
imported. A package-root import is also checked against that package's `__all__`,
so a symbol that resolves today can still be undeclared. Prefer the published
spelling: `from mvgeos_provider import SSEStreamingRealm` is checked, while
`from mvgeos_provider.sse import ...` is not, because `__all__` describes a
namespace and a submodule has none.

`mvgeos_agent` carries one extra rule, about **when** rather than **which**: do
not import it at module scope. `mvgeos_agent/__init__` imports
`mvgeos_runes.rune_runner`, which is the loader that loads Rune modules. A Rune
loaded during that chain sees a half-built `mvgeos_agent` — `Mvge` unbound,
`__all__` unset — so the import fails and the Rune silently does not load.
Import `mvgeos_agent` inside the function that uses it, which runs after the
package is fully loaded. An import under `if TYPE_CHECKING:` is fine, because
annotations are strings at runtime.

`tests/test_rune_import_boundary.py` enforces both halves: no sibling imports,
and no module-scope `mvgeos_agent` in a Rune's runtime source.

`tests/test_docs_version_claim.py` adds a third kind of gate over this
repository's own documents, so a claim that names a version cannot outlive the
code.

## Skill

A **Skill** is a capability pack: a directory with `SKILL.md` — YAML frontmatter
plus markdown instructions — matching the `agentskills.io` specification.

Skills are disclosed progressively: the catalog first (name and description),
full instructions only when relevant, bundled resources last. A Skill is never
executed directly. It is distinct from a Rune (executable extension) and a
Spell (executable tool). Skills are discovered by `skills-bridge` from three directories: `.agents/skills`
in the project, `~/.agents/skills` for the user, and the active Mvge's own
`skills/` directory. There is no `SKILL_SCOPES` constant in the engine; that Rune
enumerates the scopes and adds a fourth, for Agent Plugins, that no engine module
knows about.

## Mana, Mana Pool, Contemplation

**Mana** is tokens. **Mana Pool** is the model's context window. **Mana Used**
is cumulative consumption over a run, carried on every message-end and turn-end
event, and read by compaction to size the pool.

When a transcript crowds the Mana Pool, **compaction** replaces its earlier
part with a summary and keeps a verbatim tail. A Spell result is never a valid
cut point — it has to stay with the Invocation that requested it. Compaction is
a recovery mechanism: on any failure the run continues uncompacted, because it
must never become a new way for things to break.

**Contemplation** is reasoning depth, requested per request and passed to the
Realm as a reasoning-effort parameter. It is not the same as a run-level cap.

!!! note "Mana Budget is not implemented"
    A run-level cap on total Mana was removed from the loop — Pi has no
    equivalent, and Pi handles context pressure with compaction rather than by
    aborting. It is planned as a Rune, and will return when the loop has a
    stop-capable Sigil. No Sigil can halt a run today; only `BEFORE_SPELL_CAST`
    can veto. Do not confuse it with the per-request Contemplation budget.

## Channeling

Streaming a response from a Realm. The protocol method is `stream()`, and
`StreamRenderer` draws it. The REPL and TUI both render channeled tokens as they
arrive. (`channel` survives only as the module holding the vocabulary —
`mvgeos_core/channel.py`.)

## Where this all lives

```text
~/.agents/
├── extensions/          # Runes, including Realms
├── agents/<mvge>/       # installed Mvges, their Spells, Skills, and runes/
├── sessions/            # Tomes
├── auth/                # credentials
├── skills/              # user-scope Skills
├── models.json          # model configuration
└── mcp.json             # MCP server registry
```

Every one of those directory names and wire fields is the open `.agents`
protocol, not a MvgeOS invention. MvgeOS reserves its own vocabulary for
internal abstractions, the persona, and presentation — so that the files it
writes can be read by anything else that speaks the protocol.

## Further reading

- [Architecture](https://github.com/IAmNo1Special/mvgeos/blob/main/docs/architecture/ARCHITECTURE.md) — how the loop is put together
- [Domain glossary](https://github.com/IAmNo1Special/mvgeos/blob/main/CONTEXT.md) — the authoritative definitions
- [The `.agents` protocol](https://dotagentsprotocol.com) — the open standard MvgeOS implements
- [agent skills specification](https://agentskills.io) — the Skill format
- [Rune catalog](index.md#rune-catalog) — what is available today