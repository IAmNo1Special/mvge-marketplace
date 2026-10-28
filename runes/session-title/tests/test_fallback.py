"""Pure title-derivation rules for the session-title rune.

These are the rules a title must keep no matter which path produced it:
the fallback takes the first words of the first user prompt, titles are
cleaned of invisible characters, and no title ever splits a UTF-8
character at a byte boundary.
"""

import importlib.util
import sys
from pathlib import Path

_RUNE_PATH = Path(__file__).resolve().parent.parent / "rune.py"
_spec = importlib.util.spec_from_file_location("session_title_rune_entry", _RUNE_PATH)
assert _spec is not None and _spec.loader is not None
rune_module = importlib.util.module_from_spec(_spec)
# Dataclasses resolve their module from sys.modules at class creation,
# so the module must be registered before it is executed.
sys.modules["session_title_rune_entry"] = rune_module
_spec.loader.exec_module(rune_module)


def test_fallback_takes_the_first_eight_words_only() -> None:
    """A long first prompt becomes a short title, not a sentence."""
    title = rune_module._fallback_title(
        "one two three four five six seven eight nine ten"
    )
    assert title == "one two three four five six seven eight"


def test_fallback_collapses_whitespace_and_trims() -> None:
    """Newlines and repeated spaces in a prompt must not reach the title."""
    title = rune_module._fallback_title("  fix   the\n\nlogin\tbug  ")
    assert title == "fix the login bug"


def test_normalize_strips_control_and_invisible_characters() -> None:
    """Control codes and zero-width/bidi marks are not title characters."""
    dirty = "hello\x00\x07\u200bworld\u202e"
    assert rune_module._normalize_title(dirty) == "helloworld"


def test_truncate_bytes_never_splits_a_utf8_character() -> None:
    """A title cut at a byte limit must stay valid UTF-8, not mojibake."""
    text = "é" * 60  # 120 bytes in UTF-8, so a 96-byte cut must drop chars
    truncated = rune_module._truncate_bytes(text, 96)
    assert truncated.encode("utf-8") == b"\xc3\xa9" * 48
    assert len(truncated.encode("utf-8")) <= 96


def test_fallback_truncates_a_long_single_word_to_96_bytes() -> None:
    """A single over-long word is cut at the fallback byte budget, not kept whole."""
    title = rune_module._fallback_title("a" * 200)
    assert title == "a" * 96
