"""One file split into code with comments blanked out, plus the comments themselves.

Every rule reads a Source. Nothing here knows what a violation is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import C_LIKE, HASH_LIKE

# A slash opens a regex literal only where a value may start, never after one.
REGEX_MAY_START = re.compile(r"(^|[(,=:\[!&|?{};+\-*%~^<>]|\b(return|typeof|case|in|of|do|else|yield|await))\s*$")


@dataclass
class Comment:
    line: int
    text: str


@dataclass
class Source:
    path: Path
    lines: list[str]
    code_lines: list[str]
    comments: list[Comment]
    tree: object | None = None


def split_comments(text: str, ext: str) -> tuple[list[str], list[Comment]]:
    """Return code with comments blanked out (line-preserving) and the extracted comments."""
    line_marker = "#" if ext in HASH_LIKE else "//"
    block_open, block_close = ("/*", "*/") if ext in C_LIKE else (None, None)
    triple = ext == ".py"
    code: list[str] = []
    comments: list[Comment] = []
    i, n, line_no = 0, len(text), 1
    buf: list[str] = []
    while i < n:
        ch = text[i]
        two = text[i:i + 2]
        if ch == "\n":
            buf.append("\n"); line_no += 1; i += 1; continue
        if ch in ("'", '"', "`"):
            q = text[i:i + 3] if triple and text[i:i + 3] in ('"""', "'''") else ch
            # Only a backtick or a triple quote may span lines. An unclosed quote is prose,
            # which is how an apostrophe reaches JSX text.
            bounded = len(q) == 1 and q != "`"
            j = i + len(q)
            while j < n and text[j:j + len(q)] != q:
                if bounded and text[j] == "\n":
                    break
                if text[j] == "\\": j += 1
                if text[j:j + 1] == "\n": line_no += 1
                j += 1
            if j >= n or text[j:j + len(q)] != q:
                buf.append(ch); i += 1; continue
            span = text[i:j + len(q)]
            if triple and len(q) == 3:
                comments.append(Comment(line_no - span.count("\n"), span.strip('"\'')))
                buf.append(" " * (len(span) - span.count("\n")) + "\n" * span.count("\n"))
            else:
                buf.append(span.replace("\n", "\n"))
            i = j + len(q); continue
        if block_open and ch == "/" and two != "//" and two != "/*" and REGEX_MAY_START.search("".join(buf)[-24:]):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n" and not (text[j] == "/" and not in_class):
                if text[j] == "\\":
                    j += 1
                elif text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                j += 1
            if j < n and text[j] == "/":
                buf.append(" " * (j + 1 - i)); i = j + 1; continue
        if block_open and two == block_open:
            j = text.find(block_close, i + 2)
            j = n if j == -1 else j + 2
            span = text[i:j]
            comments.append(Comment(line_no, span))
            buf.append(re.sub(r"[^\n]", " ", span)); line_no += span.count("\n"); i = j; continue
        if text.startswith(line_marker, i) and not (line_marker == "#" and i == 0 and text.startswith("#!")):
            j = text.find("\n", i)
            j = n if j == -1 else j
            comments.append(Comment(line_no, text[i:j]))
            buf.append(" " * (j - i)); i = j; continue
        buf.append(ch); i += 1
    return "".join(buf).split("\n"), comments


def load_source(path: Path) -> Source:
    text = path.read_text(errors="replace")
    code_lines, comments = split_comments(text, path.suffix)
    return Source(path, text.split("\n"), code_lines, comments)


def hit(src: Source, line: int) -> str:
    return f"{line}: {src.lines[line - 1].strip()[:120]}"


def line_of(h: str) -> int:
    return int(h.split(":", 1)[0])


def grep_code(src: Source, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return [hit(src, i + 1) for i, l in enumerate(src.code_lines) if rx.search(l)]


def grep_comments(src: Source, pattern: str) -> list[str]:
    rx = re.compile(pattern, re.IGNORECASE)
    out = []
    for c in src.comments:
        for k, l in enumerate(c.text.split("\n")):
            if rx.search(l):
                out.append(hit(src, c.line + k)); break
    return out
