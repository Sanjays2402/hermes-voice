"""Voice cleanup."""

from __future__ import annotations

import pytest

from custom_components.hermes_voice.cleanup import clean_for_voice


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("Plain sentence.", "Plain sentence."),
        ("**bold** and *italic* and __under__ and ~~gone~~", "bold and italic and under and gone"),
        ("# Title\nBody text", "Title. Body text"),
        ("Run `ls -la` now", "Run ls -la now"),
        ("Here:\n```python\nprint('x')\n```\nDone.", "Here: Done."),
        ("Unclosed fence ```code that never ends", "Unclosed fence"),
        ("See https://example.com/a?b=c.", "See link."),
        ("Visit www.example.org today", "Visit link today"),
        ("Read [the docs](https://x.y/z) first", "Read the docs first"),
        ("![a cat](http://img/cat.png)", "a cat"),
        ("Great job 🎉🚀 captain 👍🏽!", "Great job captain!"),
        ("Flag 🇺🇸 here", "Flag here"),
        ("- one\n- two\n3. three", "one. two. three"),
        ("> quoted line", "quoted line"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "a, b. 1, 2"),
        ("too    many\t\tspaces\n\n\nand lines", "too many spaces. and lines"),
        ("<b>html</b> tags", "html tags"),
        ("Line one\n\n---\n\nLine two", "Line one. Line two"),
        ("Ends with question?\nNext", "Ends with question? Next"),
    ],
)
def test_clean_for_voice(raw: str, expected: str) -> None:
    """Each markdown/emoji/url form is removed or spoken sensibly."""
    assert clean_for_voice(raw) == expected


def test_only_code_returns_empty() -> None:
    """Pure code yields empty text (the entity then falls back to the raw stripped text)."""
    assert clean_for_voice("```\nx=1\n```") == ""
