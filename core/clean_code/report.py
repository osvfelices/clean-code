"""Turning violations into the few lines the agent reads. It never writes to the file it reports on."""
from __future__ import annotations

from pathlib import Path

from .rules import BY_ID, Violation


def line_numbers(hits: list[str]) -> str:
    return ",".join(h.split(":", 1)[0] for h in hits)


def format_report(results: dict[Path, list[Violation]], root: Path) -> str:
    lines = ["Clean up these in the file you are editing (line numbers), keep all types:"]
    for path, vs in results.items():
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = path
        lines.append(str(rel))
        for v in vs:
            if v.incomplete:
                continue
            r = BY_ID[v.rule]
            if r.whole_file:
                lines.append(f"  {r.label}: {v.hits[0]}")
            else:
                lines.append(f"  {r.label}: L{line_numbers(v.hits)}")
    return "\n".join(lines)
