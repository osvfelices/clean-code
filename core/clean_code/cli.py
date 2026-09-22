"""Entry point for the hooks and for the command line."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import Config, changed_files, project_root
from .hooks import edit_targets, edited_lines, pre_check, seen_before
from .report import autofix, format_report
from .rules import BY_ID, RULES, Violation, check_file


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "files"
    root = project_root()
    cfg = Config.load(root)
    unknown = sorted(cfg.disabled - set(BY_ID))
    if unknown:
        print(f"clean-code: .clean-code.json disables unknown rules: {', '.join(unknown)}", file=sys.stderr)
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
