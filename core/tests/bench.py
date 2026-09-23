#!/usr/bin/env python3
"""Measure the post hook on the fixed edits in bench_cases.json: what it finds, declines and prints.

Labeled cases give true and false positives and negatives; a replay of this repository's history gives
output and silence on real edits. `--json FILE` keeps every measurement, `--gate` fails on a mismatch.
"""
from __future__ import annotations

import difflib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE.parent / "clean_check.py"
REPOSITORY = HERE.parent.parent

sys.path.insert(0, str(HERE.parent))
from clean_code import BY_ID, CHECKED  # noqa: E402
from clean_code.source import get_parser  # noqa: E402

LABELS = {rule.label: rule.id for rule in BY_ID.values()}


def payload(path: Path, before: str, after: str, session: str) -> dict:
    """What Claude Code sends after writing `after` over `before`: a create, or the hunks it applied."""
    if not before:
        return {"session_id": session, "tool_name": "Write", "tool_input": {"file_path": str(path)},
                "tool_response": {"type": "create", "filePath": str(path), "content": after, "structuredPatch": []}}
    old, new = before.split("\n"), after.split("\n")
    hunks = []
    for group in difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(3):
        rows = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [" " + line for line in old[i1:i2]]
            else:
                rows += ["-" + line for line in old[i1:i2]] + ["+" + line for line in new[j1:j2]]
        hunks.append({"newStart": group[0][3] + 1, "lines": rows})
    return {"session_id": session, "tool_name": "Edit", "tool_input": {"file_path": str(path)},
            "tool_response": {"filePath": str(path), "structuredPatch": hunks}}


def post(files: dict[str, str], target: str, before: str, session: str) -> dict:
    """Run the hook once in a fresh directory and record what the agent would read."""
    with tempfile.TemporaryDirectory() as box:
        root = Path(box) / "project"
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        state = Path(box) / "state"
        state.mkdir()
        started = time.perf_counter()
        proc = subprocess.run([sys.executable, str(CHECKER), "post"], cwd=str(root), capture_output=True, text=True,
                              input=json.dumps(payload(root / target, before, files[target], session)),
                              env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin", "TMPDIR": str(state)})
        elapsed = time.perf_counter() - started
    if proc.returncode == 2:
        return {"outcome": "report", "rules": sorted(reported(proc.stderr)), "bytes": len(proc.stderr.encode()), "seconds": elapsed}
    if proc.returncode == 0 and proc.stdout:
        said = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        return {"outcome": "not_checked", "rules": [], "bytes": len(said.encode()), "seconds": elapsed}
    if proc.returncode == 0:
        return {"outcome": "silent", "rules": [], "bytes": 0, "seconds": elapsed}
    return {"outcome": "error", "rules": [], "bytes": len(proc.stderr.encode()), "seconds": elapsed, "stderr": proc.stderr[-300:]}


def reported(stderr: str) -> set[str]:
    return {LABELS[line.strip().rsplit(": ", 1)[0]] for line in stderr.splitlines()
            if line.startswith("  ") and line.strip().rsplit(": ", 1)[0] in LABELS}


def labeled(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        expected = case.get("expect_parser", case["expect"]) if get_parser else case["expect"]
        files = {case["file"]: case["after"]}
        got = post(files, case["file"], case["before"], "bench-" + case["id"])
        rows.append({"id": case["id"], "expected": expected, **got})
    return rows


def replay(settings: dict) -> list[dict]:
    """Every change to a checked file in the pinned range, posted as the edit that made it."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPOSITORY), *args], capture_output=True, text=True, check=True).stdout

    rows = []
    for commit in git("rev-list", "--reverse", f"{settings['from']}..{settings['to']}").split():
        for line in git("diff-tree", "--no-commit-id", "--name-status", "-r", commit).splitlines():
            status, name = line.split("\t")[0], line.split("\t")[-1]
            if status == "D" or Path(name).suffix not in CHECKED:
                continue
            after = git("show", f"{commit}:{name}")
            before = git("show", f"{commit}^:{name}") if status == "M" else ""
            files = {name: after}
            config = subprocess.run(["git", "-C", str(REPOSITORY), "show", f"{commit}:.clean-code.json"],
                                    capture_output=True, text=True)
            if config.returncode == 0:
                files[".clean-code.json"] = config.stdout
            rows.append({"id": f"{commit[:7]}:{name}", **post(files, name, before, "replay-" + commit[:7])})
    return rows


def summary(cases: list[dict], edits: list[dict]) -> dict:
    tp = fn = fp = tn = 0
    declined = wrong_declines = 0
    for row in cases:
        if row["outcome"] == "not_checked":
            declined += 1
            wrong_declines += row["expected"] != "not_checked"
            continue
        expected = set() if row["expected"] == "not_checked" else set(row["expected"])
        got = set(row["rules"])
        tp, fn, fp = tp + len(expected & got), fn + len(expected - got), fp + len(got - expected)
        tn += not expected and not got
    mismatches = [r["id"] for r in cases if (r["expected"] == "not_checked") != (r["outcome"] == "not_checked")
                  or (r["outcome"] != "not_checked" and sorted(r["rules"]) != sorted(set(r["expected"])))]

    def spread(rows: list[dict]) -> dict:
        times = sorted(r["seconds"] for r in rows)
        return {"median_ms": round(statistics.median(times) * 1000), "p95_ms": round(times[int(len(times) * 0.95)] * 1000),
                "max_ms": round(times[-1] * 1000)}

    kinds = {k: sum(1 for r in edits if r["outcome"] == k) for k in ("silent", "report", "not_checked", "error")}
    return {
        "parser": bool(get_parser),
        "labeled": {"cases": len(cases), "true_positive": tp, "false_negative": fn, "false_positive": fp,
                    "true_negative": tn, "not_checked": declined, "unexpected_not_checked": wrong_declines,
                    "mismatches": mismatches, **spread(cases)},
        "replay": {"edits": len(edits), **kinds, "silent_percent": round(100 * kinds["silent"] / len(edits)),
                   "bytes_total": sum(r["bytes"] for r in edits),
                   "bytes_per_edit": round(sum(r["bytes"] for r in edits) / len(edits), 1),
                   "errors": [r["id"] for r in edits if r["outcome"] == "error"], **spread(edits)},
    }


def main(argv: list[str]) -> int:
    manifest = json.loads((HERE / "bench_cases.json").read_text())
    cases, edits = labeled(manifest["cases"]), replay(manifest["replay"])
    result = summary(cases, edits)
    print(json.dumps(result, indent=1))
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps({"summary": result, "labeled": cases, "replay": edits},
                                                                  indent=1, sort_keys=True) + "\n")
    if "--gate" in argv and (result["labeled"]["mismatches"] or result["replay"]["errors"]):
        print("FAIL: a labeled case came out other than expected, or the hook failed on a replayed edit.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
