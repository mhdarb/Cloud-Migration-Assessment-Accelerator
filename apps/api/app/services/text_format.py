"""Turn model-written prose into plain text.

Chat models reply in Markdown by habit (headings, **bold**, bullet lists) even when asked
not to. Everything the app shows or exports — the UI, the report, answered client
questionnaires — treats these strings as plain text, so the syntax would appear
literally ("### Recommendation", "**Standard_D4s_v5**"). Prompts ask for plain text;
this is the guarantee.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"```[^\n]*\n?(.*?)```", re.S)
# `[ \t]` rather than `\s` at line starts: `\s` also matches newlines, so it would swallow
# the blank line separating a paragraph from the list or heading that follows it.
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*(\n|$)", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|(.+)\|[ \t]*$", re.M)
_BULLET = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.S)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RULE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.M)


def plain_text(text: str | None) -> str:
    """Strip Markdown syntax, keeping the words and the paragraph/list line breaks."""
    if not text:
        return ""
    out = _FENCE.sub(lambda m: m.group(1), text)
    out = _TABLE_RULE.sub("", out)
    # A table row becomes one line of its cells: "| SKU | D4s |" -> "SKU · D4s".
    out = _TABLE_ROW.sub(lambda m: " · ".join(c.strip() for c in m.group(1).split("|") if c.strip()), out)
    out = _RULE.sub("", out)
    out = _HEADING.sub("", out)
    out = _BULLET.sub("• ", out)
    out = _LINK.sub(r"\1", out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _EMPHASIS.sub(lambda m: m.group(2) or m.group(3) or "", out)
    lines = [" ".join(line.split()) for line in out.splitlines()]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def number(value: object) -> str:
    """500.0 -> "500", 2.5 -> "2.5" — for numbers written into prose."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
