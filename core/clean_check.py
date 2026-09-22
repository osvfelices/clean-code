#!/usr/bin/env python3
"""Clean Code enforcement hooks for Claude Code and Codex CLI.

Modes post and pre read hook JSON on stdin and exit 2 to block; files checks paths for CLI and CI.
Single-rule precision is pinned by core/tests/test_clean_check.py, the aggregate by core/tests/bench.py.
"""
from __future__ import annotations

import fnmatch
import hashlib
import tempfile
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

C_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".php", ".java", ".kt", ".swift", ".cs", ".scala"}
HASH_LIKE = {".py", ".rb", ".sh"}
TS_LIKE = {".ts", ".tsx"}
CHECKED = C_LIKE | HASH_LIKE
MAX_HITS_PER_RULE = 5

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
        unknown = sorted(set(data["disableRules"]) - {r.id for r in RULES})
        if unknown:
            print(f"clean-code: {path} disables unknown rules: {', '.join(unknown)}", file=sys.stderr)
        return Config(
            ignore=list(data["ignore"]),
            allow_console=list(data["allowConsole"]),
            allow_todo=bool(data["allowTodo"]),
            disabled=set(data["disableRules"]),
            max_comment_ratio=float(data["maxCommentRatio"]),
        )


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


@dataclass
class Violation:
    rule: str
    message: str
    hits: list[str] = field(default_factory=list)


def matches_any(path: Path, patterns: list[str], root: Path) -> bool:
    rel = str(path.resolve().relative_to(root.resolve())) if path.resolve().is_relative_to(root.resolve()) else str(path)
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch("/" + rel, p) or fnmatch.fnmatch(path.name, p) for p in patterns)


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
            j = i + len(q)
            while j < n and text[j:j + len(q)] != q:
                if text[j] == "\\": j += 1
                if text[j:j + 1] == "\n": line_no += 1
                j += 1
            span = text[i:j + len(q)]
            if triple and len(q) == 3:
                comments.append(Comment(line_no - span.count("\n"), span.strip('"\'')))
                buf.append(" " * (len(span) - span.count("\n")) + "\n" * span.count("\n"))
            else:
                buf.append(span.replace("\n", "\n"))
            i = j + len(q); continue
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


CODE_LIKE_COMMENT = re.compile(
    r"^\s*(//|#|\*)?\s*(const |let |var |return\b|if \(|for \(|while \(|import |from \S+ import|def |class |await |"
    r"\w+(\.\w+)*\(.*\)\s*;?\s*$|\w+\s*=\s*[^=].*;?\s*$|[}{]\s*$)"
)
NARRATION = re.compile(
    r"\b(added|updated|fixed|changed|removed|modified|refactored|now we|here we|this (function|method|class|file|helper) "
    r"(is|does|handles|returns|will)|helper (function|method)|utility function|initiali[sz]e the|import(s|ing)? (the|for)|"
    r"call(s|ing) the|loop (over|through) the|check(s|ing) (if|whether) the|increment|declare)\b",
    re.IGNORECASE,
)

LICENSE_NOTICE = re.compile(r"copyright|\(c\)\s*\d{4}|licen[cs]ed?\b|SPDX-License-Identifier|all rights reserved", re.I)
PROTOCOL_EXC = re.compile(r"\b(StopIteration|StopAsyncIteration|GeneratorExit)\b")
PROSE_ASSIGNMENT = re.compile(r"=\s*[A-Za-z]+(\s+[A-Za-z]+){2,}\s*$")
NO_CODE_PUNCTUATION = re.compile(r"^[^;(){}\[\]<>=\"'`]*$")
GENERATED_MARKER = re.compile(r"@generated|code generated by|do not edit|auto-?generated|automatically generated", re.I)


@dataclass(frozen=True)
class Rule:
    """A rule is data: the report, the config and the CLI all read it from here."""
    id: str
    label: str
    message: str
    check: Callable[["Source", "Config", Path], list[str]]
    languages: frozenset[str] | None = None
    whole_file: bool = False

    def applies_to(self, suffix: str) -> bool:
        return self.languages is None or suffix in self.languages


RULES: list[Rule] = []


def rule(id: str, label: str, message: str, languages: frozenset[str] | None = None, whole_file: bool = False):
    def register(check):
        RULES.append(Rule(id, label, message, check, languages, whole_file))
        return check
    return register


@rule("debug-output", "debug output",
      "Debug output left behind. Remove it or use the project logger.")
def _debug_output(src, cfg, root):
    if matches_any(src.path, cfg.allow_console, root):
        return []
    hits = grep_code(src, r"^\s*(console\.(log|debug|info|trace)\(|print\(|pprint\(|dbg!\(|var_dump\(|println!\(|fmt\.Println\(|System\.out\.print|Console\.WriteLine\(|debugger\b)")
    return [h for h in hits if not re.search(r"\bfile\s*=", h)]


@rule("narration-comment", "comment narrates or restates code: delete",
      "Comment narrates the edit or restates the code. Delete it; the code says it.")
def _narration(src, cfg, root):
    # A comment narrates the edit when the verb opens it. Mid-sentence it is ordinary prose.
    hits = []
    for c in src.comments:
        for k, l in enumerate(c.text.split("\n")):
            body = re.sub(r"^\s*(/\*\*?|\*/?|//|#)\s*", "", l)
            if NARRATION.match(body) and not re.search(r"https?://|@param|@returns|@throws|generated by|auto-?generated|do not edit", l, re.I):
                hits.append(hit(src, c.line + k)); break
    return hits


@rule("banner-comment", "banner comment: delete",
      "Section banner comment. Delete it; use blank lines and function boundaries.")
def _banner(src, cfg, root):
    rx = re.compile(r"^\s*(//|#|/\*|\*)\s*[-=*#~_]{4,}")
    hits = []
    for c in src.comments:
        if LICENSE_NOTICE.search(c.text):
            continue
        for k, l in enumerate(c.text.split("\n")):
            if rx.search(l):
                hits.append(hit(src, c.line + k)); break
    return hits


@rule("closing-brace-comment", "closing-brace comment: delete",
      "Closing-brace comment. If the block is too long to track, shorten the block.", languages=frozenset(C_LIKE))
def _closing_brace(src, cfg, root):
    return [hit(src, i + 1) for i, l in enumerate(src.lines) if re.search(r"\}\s*//\s*(end|close|\}|/?\w+\s*$)", l)]


@rule("todo-marker", "TODO/FIXME: do it or remove",
      "TODO/FIXME left in delivered code. Do it now, or track it outside the code.")
def _todo(src, cfg, root):
    return [] if cfg.allow_todo else grep_comments(src, r"\b(TODO|FIXME|XXX|HACK)\b")


def is_prose(body: str) -> bool:
    """A line with no code punctuation that reads as a sentence is prose, whatever word opens it."""
    if not NO_CODE_PUNCTUATION.match(body):
        return False
    return body.rstrip().endswith(".") or len(body.split()) >= 4


@rule("commented-code", "commented-out code: delete",
      "Commented-out code. Delete it; version control remembers.")
def _commented_code(src, cfg, root):
    hits = []
    for c in src.comments:
        body = re.sub(r"^\s*(//|#|/\*|\*)\s?", "", c.text.split("\n")[0])
        if CODE_LIKE_COMMENT.match(body) and not re.match(r"^\s*(@|https?://)", body) and len(body) > 3 \
                and not PROSE_ASSIGNMENT.search(body) and not body.startswith("   ") \
                and not is_prose(body):
            hits.append(hit(src, c.line))
    return hits


@rule("redundant-docstring", "docstring restates signature: delete",
      "Docstring restates the signature. Delete it or write what the name cannot say.")
def _redundant_doc(src, cfg, root):
    return grep_comments(src, r"^\s*(/\*\*?|///|#|\*|//)\s*(gets?|sets?|returns?|creates?|initiali[sz]es?|constructor|default constructor)\s+(the\s+|a\s+)?\w+\.?\s*(\*/)?\s*$")


@rule("suppressed-check", "silenced check: fix the cause",
      "A check was silenced. Fix the cause; if the check is wrong, fix the check in its own commit.")
def _suppressed(src, cfg, root):
    hits = grep_comments(src, r"@ts-ignore|@ts-expect-error|@ts-nocheck|eslint-disable|#\s*noqa|type:\s*ignore|pylint:\s*disable|prettier-ignore|phpcs:ignore|nolint")
    return hits + grep_code(src, r"@SuppressWarnings|#pragma warning disable|\[SuppressMessage")


@rule("weak-type", "any / loose type: write the real type",
      "`any`, `as unknown as`, or a loose type. Write the real type.", languages=frozenset(TS_LIKE))
def _weak_type(src, cfg, root):
    return grep_code(src, r"\bas any\b|:\s*any\b|<any>|\bas unknown as\b|Function\b(?!\s*\()|:\s*object\b")


@rule("non-null-assertion", "`!` assertion: narrow explicitly",
      "Non-null assertion hides a real nullability. Narrow it explicitly.", languages=frozenset(TS_LIKE))
def _non_null(src, cfg, root):
    return grep_code(src, r"[\w\)\]]!(?=[\.\)\[,;\s])(?!=)")


@rule("skipped-test", "skipped or focused test",
      "Skipped, focused, or ignored test. An ignored test is an unanswered question.")
def _skipped_test(src, cfg, root):
    # A conditional skip is a platform guard, and .fit( is not a focused test.
    return grep_code(src, r"\b(it|test|describe)\.(skip|only|todo)\(\s*['\"`]|\bx(it|describe|test)\(|(?<![.\w])f(it|describe)\(|@(pytest\.mark|unittest)\.skip(?![A-Za-z])|@Ignore\b|@Disabled\b|t\.Skip\(")


@rule("swallowed-error", "empty catch: handle or rethrow",
      "Empty catch/except. Handle it, rethrow with context, or let it propagate.")
def _empty_catch(src, cfg, root):
    joined = "\n".join(src.code_lines)
    hits = []
    for m in re.finditer(r"catch\s*(\([^)]*\))?\s*\{\s*\}|except[^:\n]*:\s*\n\s*pass\b|rescue\s*\n\s*end\b", joined):
        if PROTOCOL_EXC.search(m.group(0)):
            continue
        hits.append(hit(src, joined[:m.start()].count("\n") + 1))
    return hits


@rule("hardcoded-secret", "hardcoded secret: use env",
      "Credential in source. Read it from the environment or a secret store.")
def _secret(src, cfg, root):
    return [hit(src, i + 1) for i, l in enumerate(src.lines)
            if re.search(r"(api[_-]?key|secret|passw(or)?d|token|bearer|private[_-]?key)\w*\s*(:\s*string\s*)?[:=]\s*[\"'][A-Za-z0-9_\-/+=]{12,}[\"']", l, re.I)
            and not re.search(r"process\.env|os\.environ|getenv|\$\{|<[A-Z_]+>|example|placeholder|xxx", l, re.I)]


@rule("comment-density", "too many comment lines: rename and extract",
      "More comment lines than the ratio allows. Rename and extract instead of explaining.", whole_file=True)
def _density(src, cfg, root):
    code = sum(1 for l in src.code_lines if l.strip())
    comment = sum(c.text.count("\n") + 1 for c in src.comments)
    if code >= 40 and comment > code * cfg.max_comment_ratio:
        return [f"{comment} comment lines for {code} code lines"]
    return []


EMPHASIS = {"ALWAYS", "NEVER", "MUST", "ONLY", "CRITICAL", "IMPORTANT", "WARNING", "DANGER", "CAUTION",
            "BEWARE", "ATTENTION", "MANDATORY", "FORBIDDEN", "URGENT", "CAREFUL", "NOTE", "REMEMBER"}
CAPS_RUN = re.compile(r"\b[A-Z][A-Z0-9_]+(?:\s+[A-Z][A-Z0-9_]+){2,}\b")
ACRONYM_OK = {"API", "URL", "URI", "ID", "IDS", "UPC", "ISRC", "ISWC", "JSON", "XML", "HTTP", "HTTPS", "SQL", "CSV", "PDF", "UTC", "CPU", "RAM", "DSP", "SSR", "CSR", "UI", "UX", "JWT", "TTL", "CDN", "DNS", "IP", "TCP", "AWS", "S3", "CLI", "ENV", "OK", "MB", "GB", "KB"}


def shouts(line: str) -> bool:
    """An uppercase run only shouts when it is emphasis rather than a run of known identifiers."""
    if set(re.findall(r"(?<![-\w])[A-Z]{3,}(?![-\w])", line)) & EMPHASIS:
        return True
    return any(any(w not in ACRONYM_OK for w in run.group(0).split()) for run in CAPS_RUN.finditer(line))


@rule("comment-typography", "glyph in comment: plain text",
      "Em dash, middle dot, bullet glyph, emoji or icon in a comment. Plain sentences only.")
def _comment_typography(src, cfg, root):
    return grep_comments(src, "[\u2014\u2013\u00b7\u2022\u2192\u2713\u2717\u2705\u274c\u26a0\U0001F300-\U0001FAFF]")


@rule("comment-shouting", "ALL CAPS in comment: rewrite plainly",
      "ALL CAPS emphasis in a comment. Say it in a plain sentence.")
def _comment_shouting(src, cfg, root):
    hits = []
    for c in src.comments:
        if LICENSE_NOTICE.search(c.text):
            continue
        for k, l in enumerate(c.text.split("\n")):
            if shouts(l):
                hits.append(hit(src, c.line + k)); break
    return hits


@rule("phase-label", "task/phase label in comment: remove",
      "Task, phase or prompt label in a comment. That is the conversation's context, not the code's. Remove it.")
def _phase_label(src, cfg, root):
    return grep_comments(src, r"\b(phase|step|task|sprint|ticket|milestone|iteration)\s*[A-Z]?\d+[A-Z0-9]+\b|\b[A-Z]\d{1,3}[A-Z]?\b(?=\s+\w*(import|cutover|migrat|ingest|rollout|stage|flow|materializ))|\bper (the|our|my) (plan|prompt|conversation|spec)\b")


@rule("header-essay", "file header over four lines: move detail to the functions",
      "File header comment runs past four lines. Say what the module owns and what it deliberately does not.")
def _header_essay(src, cfg, root):
    for c in src.comments:
        if c.line > 3:
            break
        body = [l for l in c.text.split("\n") if re.sub(r"^\s*(/\*\*?|\*/?|//|#)\s*", "", l).strip()]
        if len(body) > 4:
            return [hit(src, c.line)]
    return []


BY_ID = {r.id: r for r in RULES}


def is_generated(src: Source) -> bool:
    """A generated file carries its marker up top and is not the author's to clean."""
    return any(c.line <= 5 and GENERATED_MARKER.search(c.text) for c in src.comments)


def check_file(path: Path, cfg: Config, root: Path, only_lines: set[int] | None = None) -> list[Violation]:
    if path.suffix not in CHECKED or not path.is_file() or matches_any(path, cfg.ignore, root):
        return []
    src = load_source(path)
    if is_generated(src):
        return []
    out = []
    for r in RULES:
        if r.id in cfg.disabled or not r.applies_to(path.suffix):
            continue
        if only_lines is not None and r.whole_file:
            # A whole-file property cannot be what one targeted edit introduced.
            continue
        hits = r.check(src, cfg, root)
        if not hits:
            continue
        if only_lines is not None:
            hits = [h for h in hits if line_of(h) in only_lines]
            if not hits:
                continue
        out.append(Violation(r.id, r.message, hits[:MAX_HITS_PER_RULE]))
    return out


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


def project_root() -> Path:

    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
        if top:
            return Path(top)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd()


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


WEAKENING = [
    (r"\"strict\"\s*:\s*false", "disables TypeScript strict mode"),
    (r"\"(noImplicitAny|strictNullChecks|noUnusedLocals|noUnusedParameters)\"\s*:\s*false", "disables a strictness flag"),
    (r"\"[\w@/-]+\"\s*:\s*(\"off\"|0)\b", "turns a lint rule off"),
    (r"eslint-disable|@ts-nocheck", "adds a blanket suppression"),
    (r"ignore\s*=\s*\[.*\"(E|F|B|S)\d*\"", "ignores a ruff/flake8 rule family"),
]
PROTECTED = ["**/.clean-code.json", "**/clean_check.py", "**/.clean-code/**", "**/.husky/**", "**/.pre-commit-config.yaml"]
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
        for text in news:
            for pattern, why in WEAKENING:
                if text and re.search(pattern, text):
                    return f"Blocked: this edit {why}. Fix the underlying issue; if the rule is wrong, ask the user."
        if path.suffix in TS_LIKE:
            for old, new in zip(olds, news):
                if type_annotations_removed(old, new):
                    return "Blocked: this edit removes type annotations. Keep every existing type; narrow, never loosen."
    return None


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "files"
    root = project_root()
    cfg = Config.load(root)
    if mode == "pre":
        reason = pre_check(json.load(sys.stdin), root)
        if reason:
            print(reason, file=sys.stderr); return 2
        return 0
    if mode == "post":
        payload = json.load(sys.stdin)
        results: dict[Path, list[Violation]] = {}
        for path, _, news in edit_targets(payload):
            autofix(path)
            vs = check_file(path, cfg, root, edited_lines(path, news))
            if vs:
                results[path] = vs
        if not results:
            return 0
        report = format_report(results, root)
        key = Path("|".join(sorted(str(p) for p in results)))
        if seen_before(str(payload.get("session_id", "")), key, report):
            total = sum(max(len(v.hits), 1) for vs in results.values() for v in vs)
            names = ", ".join(p.name for p in results)
            print(f"{names}: same {total} issues still open, see previous list.", file=sys.stderr)
        else:
            print(report, file=sys.stderr)
        return 2
    if mode == "rules":
        for r in RULES:
            where = "all languages" if r.languages is None else " ".join(sorted(r.languages))
            print(f"{r.id:22} {where}")
        return 0
    if mode == "explain":
        wanted = argv[2] if len(argv) > 2 else ""
        r = BY_ID.get(wanted)
        if not r:
            print(f"unknown rule: {wanted}. Run `rules` to list them.", file=sys.stderr); return 1
        print(f"{r.id}\n{r.message}")
        print(f"applies to: {'every checked language' if r.languages is None else ' '.join(sorted(r.languages))}")
        print(f"scope: {'whole file' if r.whole_file else 'the edited lines'}")
        return 0
    paths = [Path(a) for a in argv[2:]] or changed_files(root)
    results = {p: v for p in paths if (v := check_file(p, cfg, root))}
    if results:
        print(format_report(results, root)); return 1
    print("clean"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
