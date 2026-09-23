"""Which files the checker may look at, the per-project overrides and where the project starts."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


C_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".php", ".java", ".kt", ".swift", ".cs", ".scala"}


HASH_LIKE = {".py", ".rb", ".sh"}


TS_LIKE = {".ts", ".tsx"}


JS_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


CHECKED = C_LIKE | HASH_LIKE


DEFAULT_CONFIG = {
    "ignore": ["**/node_modules/**", "**/dist/**", "**/build/**", "**/.next/**", "**/vendor/**", "**/*.d.ts", "**/*.min.js"],
    "allowConsole": ["**/scripts/**", "**/bin/**", "**/cli/**"],
    "allowTodo": False,
    "disableRules": [],
    "maxCommentRatio": 0.25,
    "maxArguments": 5,
}


class ConfigError(ValueError):
    """The project's .clean-code.json asks for something the checker cannot honour."""


def is_patterns(value) -> bool:
    return isinstance(value, list) and all(isinstance(p, str) and p for p in value)


def is_ratio(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1


def is_argument_limit(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 20


SCHEMA = {
    "ignore": (is_patterns, "a list of glob patterns"),
    "allowConsole": (is_patterns, "a list of glob patterns"),
    "allowTodo": (lambda v: isinstance(v, bool), "true or false"),
    "disableRules": (lambda v: isinstance(v, list) and all(isinstance(r, str) for r in v), "a list of rule ids"),
    "maxCommentRatio": (is_ratio, "a number above 0 and at most 1"),
    "maxArguments": (is_argument_limit, "a whole number from 1 to 20"),
}


def validated(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON at line {exc.lineno}") from exc
    except OSError as exc:
        raise ConfigError(f"{path.name} cannot be read: {exc.strerror}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a JSON object")
    for key, value in data.items():
        if key not in SCHEMA:
            raise ConfigError(f'{path.name} has an unknown key "{key}"; the keys are {", ".join(sorted(SCHEMA))}')
        valid, meaning = SCHEMA[key]
        if not valid(value):
            raise ConfigError(f'"{key}" must be {meaning}')
    # Imported here: the rules read this module, and the registry is complete once they are all loaded.
    from .ast_rules import RULE_IDS
    from .rules import BY_ID
    for rule_id in data.get("disableRules", []):
        if rule_id not in set(BY_ID) | RULE_IDS:
            raise ConfigError(f'unknown rule "{rule_id}" in "disableRules"; run `clean-code rules` for the list')
    return data


@dataclass
class Config:
    ignore: list[str]
    allow_console: list[str]
    allow_todo: bool
    disabled: set[str]
    max_comment_ratio: float
    max_arguments: int

    @staticmethod
    def load(root: Path) -> "Config":
        """The project's .clean-code.json over the defaults. Anything the checker cannot honour raises."""
        data = dict(DEFAULT_CONFIG)
        path = root / ".clean-code.json"
        if path.is_file():
            data.update(validated(path))
        return Config(
            ignore=list(data["ignore"]),
            allow_console=list(data["allowConsole"]),
            allow_todo=bool(data["allowTodo"]),
            disabled=set(data["disableRules"]),
            max_comment_ratio=float(data["maxCommentRatio"]),
            max_arguments=int(data["maxArguments"]),
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
