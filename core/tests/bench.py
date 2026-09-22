#!/usr/bin/env python3
"""Measure hook output against real codebases. Unit tests pin single cases, this pins the aggregate.

Corpora are whatever exists locally; pass directories to override. Fails when the average
per-edit output exceeds the ceiling, which is what a precision regression looks like.
"""
from __future__ import annotations

import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent / "clean_check.py"
CEILING_BYTES = 25
SAMPLE = 40


def candidates(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) for a in argv]
    found = [Path.home() / "corpora/vscode/src"]
    found += list(Path(sys.base_prefix, "lib").glob("python3*"))
    seen, out = set(), []
    for p in found:
        key = p.resolve()
        if p.is_dir() and key not in seen:
            seen.add(key); out.append(p)
    return out


def sample_files(root: Path, limit: int) -> list[Path]:
    files = [p for p in root.rglob("*") if p.suffix in {".ts", ".tsx", ".py"} and p.is_file()
             and "node_modules" not in p.parts and "__pycache__" not in p.parts]
    random.shuffle(files)
    return files[:limit]


def hook_bytes(path: Path, edited: str, root: Path) -> int:
    payload = {"session_id": f"bench{random.randint(1, 10 ** 9)}",
               "tool_input": {"file_path": str(path), "old_string": "\u0000absent", "new_string": edited}}
    proc = subprocess.run([sys.executable, str(CHECKER), "post"], input=json.dumps(payload), capture_output=True,
                          text=True, env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"})
    return len(proc.stderr)


def main(argv: list[str]) -> int:
    random.seed(11)
    roots = candidates(argv)
    if not roots:
        print("no corpora found, pass directories as arguments")
        return 0
    sizes: list[int] = []
    for root in roots:
        picked = sample_files(root, SAMPLE)
        for path in picked:
            lines = [l for l in path.read_text(errors="replace").split("\n") if l.strip()]
            if lines:
                sizes.append(hook_bytes(path, random.choice(lines), root))
        print(f"  {len(picked):4} files  {root}")
    if not sizes:
        print("no files sampled")
        return 0
    mean = statistics.mean(sizes)
    silent = sum(1 for s in sizes if s == 0) * 100 // len(sizes)
    print(f"\n  edits sampled  {len(sizes)}")
    print(f"  mean output    {mean:.1f} bytes (ceiling {CEILING_BYTES})")
    print(f"  median output  {statistics.median(sizes):.0f} bytes")
    print(f"  silent edits   {silent}%")
    if mean > CEILING_BYTES:
        print(f"\nFAIL: {mean:.1f} bytes per edit exceeds the {CEILING_BYTES} byte ceiling.")
        return 1
    print("\nok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
