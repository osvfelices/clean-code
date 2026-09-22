"""Which files the checker may look at, the per-project overrides and where the project starts."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


C_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".php", ".java", ".kt", ".swift", ".cs", ".scala"}


HASH_LIKE = {".py", ".rb", ".sh"}


TS_LIKE = {".ts", ".tsx"}


CHECKED = C_LIKE | HASH_LIKE


DEFAULT_CONFIG = {
    "ignore": ["**/node_modules/**", "**/dist/**", "**/build/**", "**/.next/**", "**/vendor/**", "**/*.d.ts", "**/*.min.js"],
    "allowConsole": ["**/scripts/**", "**/bin/**", "**/cli/**"],
    "allowTodo": False,
    "disableRules": [],
    "maxCommentRatio": 0.25,
}


@dataclass
class Config:
    ignore: list[str]
    allow_console: list[str]
    allow_todo: bool
    disabled: set[str]
    max_comment_ratio: float

    @staticmethod
    def load(root: Path) -> "Config":
        data = dict(DEFAULT_CONFIG)
        path = root / ".clean-code.json"
        if path.is_file():
            try:
                data.update(json.loads(path.read_text()))
            except json.JSONDecodeError as exc:
                print(f"clean-code: ignoring malformed {path}: {exc}", file=sys.stderr)
        return Config(
            ignore=list(data["ignore"]),
            allow_console=list(data["allowConsole"]),
            allow_todo=bool(data["allowTodo"]),
            disabled=set(data["disableRules"]),
            max_comment_ratio=float(data["maxCommentRatio"]),
        )


def matches_any(path: Path, patterns: list[str], root: Path) -> bool:
    rel = str(path.resolve().relative_to(root.resolve())) if path.resolve().is_relative_to(root.resolve()) else str(path)
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch("/" + rel, p) or fnmatch.fnmatch(path.name, p) for p in patterns)


def project_root() -> Path:

    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    return Path(top) if top else Path.cwd()


def changed_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    files = []
    for line in out.splitlines():
        if len(line) > 3 and line[0] != "D" and line[1] != "D":
            files.append(root / line[3:].split(" -> ")[-1].strip('"'))
    return files
