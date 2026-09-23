"""The hook protocol: which lines an edit introduced, what to block before it, what was said before."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .config import TS_LIKE, matches_any


class NoEdit(ValueError):
    """The hook input does not name an edit, so nothing about it can be called clean."""


def edited_lines(payload: dict) -> list[tuple[Path, set[int] | None]]:
    """Each file an edit wrote and the lines it introduced there, or None where those cannot be established.

    An empty set is an edit that introduced nothing. An input that names no edit raises NoEdit instead.
    """
    inp = payload.get("tool_input")
    if not isinstance(inp, dict):
        raise NoEdit
    cwd = Path(payload.get("cwd") or os.getcwd())
    patch = inp.get("command", "")
    if payload.get("tool_name") == "apply_patch" or (isinstance(patch, str) and "*** Begin Patch" in patch):
        files = patch_files(patch, cwd) if isinstance(patch, str) else []
        if not files:
            raise NoEdit
        return [(path, confirmed(path, patch_additions(op, body, path))) for op, path, body in files if op != "Delete"]
    target = inp.get("file_path") or inp.get("path")
    if not isinstance(target, str) or not target:
        raise NoEdit
    path = Path(target) if Path(target).is_absolute() else cwd / target
    response = payload.get("tool_response")
    return [(path, confirmed(path, claude_additions(response) if isinstance(response, dict) else None))]


def claude_additions(response: dict) -> dict[int, str] | None:
    """Line number to text for every line Claude Code reports adding, read from its structuredPatch."""
    if response.get("type") == "create":
        return dict(enumerate(str(response.get("content", "")).split("\n"), 1))
    hunks = response.get("structuredPatch")
    if not isinstance(hunks, list):
        return None
    added: dict[int, str] = {}
    for hunk in hunks:
        line = hunk.get("newStart")
        if not isinstance(line, int):
            return None
        for row in hunk.get("lines", []):
            if row[:1] == "+":
                added[line] = row[1:]
            if row[:1] in (" ", "+"):
                line += 1
    return added


def patch_additions(op: str, body: list[str], path: Path) -> dict[int, str] | None:
    """Line number to text for every line a Codex patch added, located the way Codex applies it.

    A patch carries no line numbers. Each hunk is found by its context, after the previous hunk and
    its @@ anchor. A hunk that matches more than one place makes the whole file's scope unknown.
    """
    if op == "Add":
        return {i: row[1:] for i, row in enumerate(body, 1) if row[:1] == "+"}
    lines = read_lines(path)
    if lines is None:
        return None
    added: dict[int, str] = {}
    cursor = 0
    for anchor, hunk in patch_hunks(body):
        if anchor:
            found = next((i for i in range(cursor, len(lines)) if lines[i].strip() == anchor), None)
            if found is None:
                return None
            cursor = found + 1
        if not any(row[:1] == "+" and row[1:].strip() for row in hunk):
            continue
        after = [row[1:] for row in hunk if row[:1] in (" ", "+", "")]
        places = [s for s in range(cursor, len(lines) - len(after) + 1)
                  if all(lines[s + k].rstrip() == text.rstrip() for k, text in enumerate(after))]
        if len(places) != 1:
            return None
        k = places[0]
        for row in hunk:
            if row[:1] == "+":
                added[k + 1] = row[1:]
            if row[:1] in (" ", "+", ""):
                k += 1
        cursor = k
    return added


def patch_hunks(body: list[str]) -> list[tuple[str, list[str]]]:
    hunks: list[tuple[str, list[str]]] = [("", [])]
    for row in body:
        if row.startswith("@@"):
            hunks.append((row[2:].strip(), []))
        else:
            hunks[-1][1].append(row)
    return [h for h in hunks if h[0] or h[1]]


def confirmed(path: Path, added: dict[int, str] | None) -> set[int] | None:
    """The added lines, provided the file still holds them there. A formatter or a person may have moved them."""
    if added is None:
        return None
    lines = read_lines(path)
    if lines is None:
        return None
    if any(n > len(lines) or lines[n - 1] != text.rstrip("\r") for n, text in added.items()):
        return None
    return set(added)


def read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None


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


PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$")


def patch_files(patch: str, cwd: Path) -> list[tuple[str, Path, list[str]]]:
    """Split a Codex apply_patch body into (operation, path, body rows) per file."""
    files: list[list] = []
    for line in patch.splitlines():
        head, move = PATCH_FILE.match(line), PATCH_MOVE.match(line)
        if head or move:
            name = Path((head.group(2) if head else move.group(1)).strip())
            path = name if name.is_absolute() else cwd / name
            if head:
                files.append([head.group(1), path, []])
            elif files:
                files[-1][1] = path
            continue
        if files and not line.startswith("***"):
            files[-1][2].append(line)
    return [(op, path, body) for op, path, body in files]


def patch_sections(patch: str, cwd: Path) -> list[tuple[Path, str, str]]:
    """Split a Codex apply_patch body into (path, removed text, added text) per file."""
    return [(path, "\n".join(r[1:] for r in body if r[:1] == "-"), "\n".join(r[1:] for r in body if r[:1] == "+"))
            for _, path, body in patch_files(patch, cwd)]


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
