# Rune hosting, Phase 4 v1

Phase 4 v1 hosts spell, hook, command, and skill runes
out-of-process (one host process per hosted rune, stdio JSON
wire, scrubbed environment). Realm, spell-gateway, and approval
runes remain in-process in v1, protected by Phase 3 pinning.
Realm hosting is Phase 4b, a separate decision.

Honest claim, do not upgrade it: after Phase 4, third-party
spell/hook/command rune code runs outside the engine process,
with a declared, audited capability surface; realm, gateway,
and approval runes remain in-process and pinned. Phase 4 is not
a credential or filesystem boundary: a hostile host rune can
still read files its OS user can read and reach the network.
Pinned means unmodified, not benign. Hosted means
out-of-process, not confined.

## How a rune is classified

Classification is engine-side
(`mvge.runes.host_classification.classify_manifest`), in this
order: a manifest `hosting: "in-process"` declaration wins;
then the tier rules (approval-rune by name, spell-gateway flag,
`realm` capability / `RealmProvider` type, `Codec` type or
`session_codecs`, `Spell Gate` type, `Spell Modifier` type);
then an unrecognized `hosting` value fails closed to
in-process. Anything else is eligible — eligible is not
proven: an eligible rune still needs its own hosted
verification before it is hosted by default.

The test `tests/test_rune_hosting_v1.py` is the gate: it
asserts the in-process set below, asserts no rune declares
`hosting: "hosted"` while belonging to the tier, and asserts
the eligible set exactly, so a new rune (or a changed
manifest) forces a deliberate update to this table.

## In-process-only in v1

| Rune | Mechanism | Reason |
|---|---|---|
| approval-rune | manifest flag + name rule | Approval channel and spell gates never cross the wire. |
| opencode-realm | manifest flag + realm rule | Realm factory receives the provider key from the engine; no streaming realm wire in v1 (Phase 4b). |
| openrouter-realm | manifest flag + realm rule | Same as opencode-realm. |
| pollinations-realm | manifest flag + realm rule | Same as opencode-realm. |
| seeker | manifest flag + gateway rule + modifier rule | Spell-gateway rune; also a spell modifier, and it uses the engine-held model call (`stream_model_text`), which is unsupported in a host. |
| session-title | manifest flag | Uses the engine-held model call (`stream_model_text`) for title generation; that call is in-process-only in v1. |
| pi-codec | manifest flag + codec rule | Session codec is a live object; its factory wires nothing over the facade, and codecs have no wire channel in v1. |
| codeact-rune | manifest flag | Dispatches live spell objects and executes code against a live `SpellContext`; nested live spell dispatch has no wire channel in v1. |
| submvge-bridge | manifest flag | Uses `get_registered_realm_factories` and `approval_presenter_source`, both unsupported in a host. |
| mcp-bridge | modifier rule (no flag needed) | Registers spell modifiers; live spell-call mutation has no wire channel in v1. |

## Eligible in v1 (not yet proven hosted by default)

adr-bridge, checkpoint-bridge, eval-bridge, lsp-bridge,
okf-bridge, opentelemetry-bridge, selfmod-bridge,
session-search, skill-evolution, skills-bridge,
steering-bridge.

Eligible means the engine will host them when asked
(`RuneRunner.host_rune`); it does not mean any of them has
been verified hosted end to end. A rune moves from this list
to "verified hosted" only with a hosted run of its own suite
behind it — that verification is follow-up work, not claimed
here. Spells in these runes that require a live backend or a
non-serializable context object stay in-process-only per cast
(the engine refuses such a cast closed, even for a hosted
rune), and hosted hooks receive plain JSON dicts, not typed
dataclass objects.
