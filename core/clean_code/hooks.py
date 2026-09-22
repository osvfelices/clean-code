"""The hook protocol: which lines an edit introduced, what to block before it, what was said before."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .config import TS_LIKE, matches_any


def edited_lines(path: Path, news: list[str]) -> set[int] | None:
    """Lines this edit introduced. None when the edit cannot be located and the whole file stands."""
    wanted = {l.strip() for n in news if n for l in n.split("\n") if l.strip()}
    if not wanted:
        return None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    return {i + 1 for i, l in enumerate(text.split("\n")) if l.strip() in wanted}


def seen_before(session: str, path: Path, report: str) -> bool:
    """True when this exact report was already sent for this file in this session."""
    key = hashlib.sha1(f"{session}:{path.resolve()}".encode()).hexdigest()[:16]
    state = Path(tempfile.gettempdir()) / "clean-code-state" / key
    digest = hashlib.sha1(report.encode()).hexdigest()
    if state.is_file() and state.read_text() == digest:
        return True
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(digest)
    return False


WEAKENING = [
    (r"\"strict\"\s*:\s*false", "disables TypeScript strict mode"),
    (r"\"(noImplicitAny|strictNullChecks|noUnusedLocals|noUnusedParameters)\"\s*:\s*false", "disables a strictness flag"),
    (r"\"[\w@/-]+\"\s*:\s*(\"off\"|0)\b", "turns a lint rule off"),
    (r"eslint-disable|@ts-nocheck", "adds a blanket suppression"),
    (r"ignore\s*=\s*\[.*\"(E|F|B|S)\d*\"", "ignores a ruff/flake8 rule family"),
]


PROSE = {".md", ".mdx", ".txt", ".rst", ".adoc"}
PROTECTED = ["**/.clean-code.json", "**/clean_check.py", "**/clean_code/*.py", "**/.clean-code/**", "**/.husky/**", "**/.pre-commit-config.yaml"]


TYPE_ANNOTATION = re.compile(r"[\w\)\]]\s*:\s*(?!\s*\{)[A-Za-z_][\w<>\[\]|&., ]*")


def type_annotations_removed(old: str, new: str) -> bool:
    if not old or not new:
        return False
    before, after = len(TYPE_ANNOTATION.findall(old)), len(TYPE_ANNOTATION.findall(new))
    return after < before and len(new.splitlines()) >= len(old.splitlines()) * 0.8


def patch_sections(patch: str, cwd: Path) -> list[tuple[Path, str, str]]:
    """Split a Codex apply_patch body into (path, removed text, added text) per file."""
    sections: list[list] = []
    for line in patch.splitlines():
        head = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
        move = re.match(r"^\*\*\* Move to: (.+)$", line)
        if head or move:
            name = Path((head.group(2) if head else move.group(1)).strip())
            path = name if name.is_absolute() else cwd / name
            if move and sections:
                sections[-1][0] = path
            else:
                sections.append([path, [], []])
            continue
        if not sections or line.startswith("***"):
            continue
        if line.startswith("+"):
            sections[-1][2].append(line[1:])
        elif line.startswith("-"):
            sections[-1][1].append(line[1:])
    return [(p, "\n".join(old), "\n".join(new)) for p, old, new in sections]


def edit_targets(payload: dict) -> list[tuple[Path, list[str], list[str]]]:
    """Normalize Claude Code Edit/Write/MultiEdit and Codex apply_patch into (path, olds, news)."""
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input", {}) or {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    patch = inp.get("command", "") if isinstance(inp, dict) else ""
    if tool == "apply_patch" or (isinstance(patch, str) and "*** Begin Patch" in patch):
        return [(p, [old], [new]) for p, old, new in patch_sections(patch, cwd)]
    target = inp.get("file_path") or inp.get("path")
    if not target:
        return []
    path = Path(target) if Path(target).is_absolute() else cwd / target
    olds = [inp.get("old_string", "")] + [e.get("old_string", "") for e in inp.get("edits", [])]
    news = [inp.get("new_string", ""), inp.get("content", "")] + [e.get("new_string", "") for e in inp.get("edits", [])]
    return [(path, olds, news)]


def pre_check(payload: dict, root: Path) -> str | None:
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input", {}) or {}
    if tool == "Bash":
        cmd = inp.get("command", "") if isinstance(inp, dict) else ""
        if "*** Begin Patch" not in cmd:
            if re.search(r"--no-verify|\bgit commit\s+-\w*n\b|\bgit push\s+.*(--force\b|-f\b).*\b(main|master)\b", cmd):
                return f"Blocked: this command bypasses safeguards ({cmd[:100]}). Fix the failing check instead."
            if re.search(r"\b(rm|git rm)\b.*\.(test|spec)\.(ts|tsx|js|py)\b", cmd):
                return "Blocked: deleting a test file. Fix or rewrite the test; ask the user before removing it."
            return None
    for path, olds, news in edit_targets(payload):
        if path.name and matches_any(path, PROTECTED, root):
            return f"Blocked: {path.name} protects code quality. Ask the user before changing it."
        # Prose can quote a setting without turning it off.
        for text in ([] if path.suffix in PROSE else news):
            for pattern, why in WEAKENING:
                if text and re.search(pattern, text):
                    return f"Blocked: this edit {why}. Fix the underlying issue; if the rule is wrong, ask the user."
        if path.suffix in TS_LIKE:
            for old, new in zip(olds, news):
                if type_annotations_removed(old, new):
                    return "Blocked: this edit removes type annotations. Keep every existing type; narrow, never loosen."
    return None
