"""The one rule that reads past the file it is given: what a browser bundle must not reach.

A client module that pulls a runtime value from a module reaching a Node-only package fails at
build time, after types and tests have passed. Two hops is what it takes to see it, measured.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .rules import rule
from .source import Source, hit

# Depth 2 catches a client importing a lib that imports the driver. Depth 1 misses it, depth 3 adds
# nothing on a 694 file corpus. The budget stops a pathological graph from stalling an edit.
MAX_DEPTH = 2
MAX_FILES = 60

BOUNDARY_LANGUAGES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
RESOLVED_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")

# Packages with no browser build. @prisma/client and bcryptjs ship one, so they do not belong here.
NODE_ONLY = frozenset({
    "fs", "net", "dns", "tls", "http", "https", "child_process", "cluster", "worker_threads", "dgram",
    "ioredis", "redis", "pg", "mysql", "mysql2", "mongodb", "bullmq", "nodemailer", "bcrypt",
    "sharp", "puppeteer", "playwright", "aws-sdk",
})

IMPORT = re.compile(r"""^\s*(?:import|export)\s+(type\s+)?(?:[\w*{},\s]+?\s+from\s+)?['"]([^'"]+)['"]""", re.M)
DIRECTIVE = re.compile(r"""^['"]use (client|server)['"]""")


def directive(code_lines: list[str]) -> str | None:
    """The first real statement, read from code with comments already blanked out."""
    for line in code_lines:
        if line.strip():
            found = DIRECTIVE.match(line.strip())
            return found.group(1) if found else None
    return None


def is_node_only(spec: str) -> bool:
    return spec.startswith("node:") or spec in NODE_ONLY or spec.split("/")[0] in NODE_ONLY


def resolve(spec: str, origin: Path, root: Path) -> Path | None:
    if spec.startswith("@/"):
        base = root / spec[2:]
    elif spec.startswith("."):
        base = (origin.parent / spec).resolve()
    else:
        return None
    for candidate in [base.with_suffix(s) for s in RESOLVED_SUFFIXES] + [base / f"index{s}" for s in RESOLVED_SUFFIXES]:
        if candidate.is_file():
            return candidate
    return None


def reaches_node_only(src: Source, root: Path) -> str | None:
    """Walk the value imports out of a client module until something Node-only turns up."""
    queue, seen, opened = [(src.path, "\n".join(src.code_lines), 0)], set(), 0
    while queue:
        path, text, depth = queue.pop(0)
        opened += 1
        if opened > MAX_FILES:
            return None
        for is_type, spec in IMPORT.findall(text):
            if is_type:
                continue
            if is_node_only(spec):
                return spec
            if depth >= MAX_DEPTH:
                continue
            target = resolve(spec, path, root)
            if target is None or target in seen:
                continue
            try:
                body = target.read_text(errors="replace")
            except OSError:
                continue
            # A server module is an RPC boundary. Its imports never reach the browser bundle.
            if body.lstrip()[:12] in ('"use server"', "'use server'"):
                continue
            seen.add(target)
            queue.append((target, body, depth + 1))
    return None


@rule("client-bundles-server-code", "client module reaches a server-only package",
      "A browser bundle cannot contain this. Import the type, or move the value behind the server boundary.",
      languages=BOUNDARY_LANGUAGES)
def _client_bundles_server_code(src: Source, cfg: Config, root: Path) -> list[str]:
    if directive(src.code_lines) != "client":
        return []
    reached = reaches_node_only(src, root)
    return [hit(src, 1)] if reached else []
