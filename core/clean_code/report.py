"""Turning violations into the few lines the agent reads, and fixing what needs no judgement."""
from __future__ import annotations

import re
from pathlib import Path

from .config import CHECKED
from .rules import BY_ID, Violation
from .source import load_source


MAX_LINES_PER_RULE = 8


def line_numbers(hits: list[str]) -> str:
    nums = [h.split(":", 1)[0] for h in hits]
    shown = ",".join(nums[:MAX_LINES_PER_RULE])
    return shown + (f" +{len(nums) - MAX_LINES_PER_RULE}" if len(nums) > MAX_LINES_PER_RULE else "")


def format_report(results: dict[Path, list[Violation]], root: Path) -> str:
    lines = ["Clean up these in the file you are editing (line numbers), keep all types:"]
    for path, vs in results.items():
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = path
        lines.append(str(rel))
        for v in vs:
            r = BY_ID.get(v.rule)
            label = r.label if r else v.message
            if r and r.whole_file:
                lines.append(f"  {label}: {v.hits[0]}")
            else:
                lines.append(f"  {label}: L{line_numbers(v.hits)}" if v.hits else f"  {label}")
    return "\n".join(lines)


GLYPH_FIXES = [
    (re.compile(r"\s*[\u2014\u2013]\s*"), ", "),
    (re.compile(r"\s+\u00b7\s+"), "; "),
    (re.compile(r"\u00b7"), ","),
    (re.compile(r"\s*\u2192\s*"), " to "),
    (re.compile("[\u2022\u2713\u2717\u2705\u274c\u26a0\U0001F300-\U0001FAFF]\\s?"), ""),
]


BANNER_ONLY = re.compile(r"^\s*(//|#)\s*[-=*#~_]{4,}.*$")


CLOSING = re.compile(r"(\})\s*//\s*(end|close)\b.*$")


def autofix(path: Path) -> int:
    """Rewrite comment glyphs, drop banner lines and closing-brace markers. Never touches code."""
    if path.suffix not in CHECKED or not path.is_file():
        return 0
    src = load_source(path)
    lines = list(src.lines)
    fixes = 0
    drop: set[int] = set()
    for c in src.comments:
        for k, text in enumerate(c.text.split("\n")):
            idx = c.line - 1 + k
            if idx >= len(lines):
                break
            if k == 0 and BANNER_ONLY.match(lines[idx]):
                drop.add(idx)
                fixes += 1
                continue
            fixed = text
            for rx, rep in GLYPH_FIXES:
                fixed = rx.sub(rep, fixed)
            if fixed != text and text in lines[idx]:
                lines[idx] = lines[idx].replace(text, fixed, 1)
                fixes += 1
    for i, line in enumerate(lines):
        m = CLOSING.search(line)
        if m and i not in drop:
            lines[i] = line[:m.start()] + "}"
            fixes += 1
    if fixes:
        path.write_text("\n".join(line for i, line in enumerate(lines) if i not in drop))
    return fixes
