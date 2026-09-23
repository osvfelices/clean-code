"""Entry point for the hooks and for the command line."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import Config, ConfigError, changed_files, project_root
from .hooks import NoEdit, edited_lines, pre_check, seen_before
from .report import format_report
from .rules import BY_ID, RULES, Violation, check_file
from .source import Unparsable


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "files"
    if mode in ("install", "uninstall"):
        from .install import main as install_main
        return install_main(argv[2:] + (["--uninstall"] if mode == "uninstall" else []))
    root = project_root()
    if mode == "pre":
        reason = pre_check(json.load(sys.stdin), root)
        if reason:
            print(reason, file=sys.stderr); return 2
        return 0
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
    if mode not in ("post", "files"):
        print(f"clean-code: unknown command {mode}. Run `clean-code --help`.", file=sys.stderr); return 1
    try:
        cfg = Config.load(root)
    except ConfigError as exc:
        reason = f"not checked, invalid clean-code configuration: {exc}"
        if mode == "post":
            return tell_agent([reason])
        print(f"clean-code: {reason}"); return 1
    if mode == "post":
        return post(sys.stdin.read(), cfg, root)
    paths = [Path(a) for a in argv[2:]] or changed_files(root)
    results, unchecked = {}, []
    for p in paths:
        try:
            vs = check_file(p, cfg, root)
        except Unparsable as exc:
            unchecked.append(f"{p}: not checked, {exc}")
            continue
        unchecked += [f"{p}: {v.rule} not checked, {v.incomplete}" for v in vs if v.incomplete]
        found = [v for v in vs if not v.incomplete]
        if found:
            results[p] = found
    if results:
        print(format_report(results, root))
    if unchecked:
        print("\n".join(unchecked))
    if results or unchecked:
        return 1
    print("clean"); return 0


def post(raw: str, cfg: Config, root: Path) -> int:
    """Check what an edit introduced and answer the agent on the channel it reads.

    A finding exits 2 with the report on stderr. A file that could not be checked exits 0 with a JSON
    additionalContext note, the one channel both Claude Code and Codex hand to the model without
    blocking; stderr on exit 0 never reaches it. Checked and clean exits 0 with no output at all.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    try:
        edits = edited_lines(payload) if isinstance(payload, dict) else None
    except NoEdit:
        edits = None
    if edits is None:
        return tell_agent(["not checked, the hook input names no edit to check"])
    results: dict[Path, list[Violation]] = {}
    unchecked = []
    for path, lines in edits:
        if lines is None:
            unchecked.append(f"{path.name} not checked, the edited lines could not be located")
            continue
        if not lines:
            continue
        try:
            vs = check_file(path, cfg, root, lines)
        except Unparsable as exc:
            unchecked.append(f"{path.name} not checked, {exc}")
            continue
        unchecked += [f"{path.name} {v.rule} not checked, {v.incomplete}" for v in vs if v.incomplete]
        found = [v for v in vs if not v.incomplete]
        if found:
            results[path] = found
    if not results:
        return tell_agent(unchecked) if unchecked else 0
    report = format_report(results, root)
    key = Path("|".join(sorted(str(p) for p in results)))
    if seen_before(str(payload.get("session_id", "")), key, report):
        total = sum(max(len(v.hits), 1) for vs in results.values() for v in vs)
        names = ", ".join(p.name for p in results)
        report = f"{names}: same {total} issues still open, see previous list."
    print("\n".join([report] + [f"clean-code: {note}" for note in unchecked]), file=sys.stderr)
    return 2


def tell_agent(notes: list[str]) -> int:
    context = "\n".join(f"clean-code: {note}" for note in notes)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    return 0
