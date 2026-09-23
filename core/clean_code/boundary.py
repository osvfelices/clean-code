"""The one rule that reads past its file: a "use client" module must not gain a runtime import path,
through modules this rule can resolve, to one that cannot run in a browser. A "use server" module ends
a path, since a client reaches it by RPC. An edited import it cannot resolve, or a graph past its
budget, is reported as not checked, never as clean."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import JS_LIKE, Config
from .rules import Incomplete, rule
from .source import Source, Unparsable, blank, hit, read

# Modules a path may open before the answer is called unknown. A client page on a production Next.js
# app reaches a few hundred; the budget keeps a pathological graph from stalling an edit.
MAX_MODULES = 2000

# Files a search for the modules importing an edited one may consider.
MAX_PROJECT_FILES = 20000

# Output and dependency folders, and any hidden one such as .next or a tool's worktrees.
SKIPPED_DIRECTORIES = {"node_modules", "dist", "build", "out", "coverage"}

# Any quoted text without spaces, which is where every specifier a file holds is written.
QUOTED_PATH = re.compile(r"""['"`]([^'"`\s]+)['"`]""")

# Node builtins with no browser version in a Next.js client build. Next 16 substitutes assert, buffer,
# constants, crypto, domain, events, http, https, os, path, process, punycode, querystring, stream,
# string_decoder, sys, timers, tty, url, util, vm and zlib (next/dist/build/webpack-config.js).
SERVER_BUILTINS = frozenset({
    "async_hooks", "child_process", "cluster", "dgram", "diagnostics_channel", "dns", "fs", "http2", "inspector",
    "module", "net", "perf_hooks", "readline", "repl", "tls", "trace_events", "v8", "wasi", "worker_threads",
})

# Imports Next.js itself refuses in a client bundle.
SERVER_MARKERS = frozenset({"server-only", "next/headers"})

# Packages that ship no browser build. One installed with a browser entry in its package.json is taken
# at its word instead.
NODE_PACKAGES = frozenset({
    "ioredis", "redis", "pg", "mysql", "mysql2", "mongodb", "bullmq", "nodemailer", "bcrypt", "sharp",
    "puppeteer", "playwright", "aws-sdk",
})

SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

# Matched against the code view, where a string literal keeps its quotes and loses its contents, so
# the specifier is read back from the same span of the source.
QUOTED = r"(?P<q>['\"`])(?P<spec> *)(?P=q)"
FROM = re.compile(r"(?<![\w.$])(?:import|export)(?P<clause>[\w\s{},*$]*?)\bfrom\s*" + QUOTED)
BARE = re.compile(r"(?<![\w.$])import\s*" + QUOTED)
CALL = re.compile(r"(?<![\w.$])(?:import|require)\s*\(\s*(?:" + QUOTED + r"\s*\)|(?P<other>[^)\s]))")
DIRECTIVE = re.compile(r"""^['"]use (client|server)['"]""")


@dataclass(frozen=True)
class Edge:
    """One runtime import: the lines it spans and what it names, None for a computed specifier."""
    first: int
    last: int
    spec: str | None


@dataclass
class Module:
    directive: str | None
    edges: list[Edge]


def directive(code_lines: list[str]) -> str | None:
    """The directive in the prologue, read from code with comments already blanked out."""
    for line in code_lines:
        if line.strip():
            found = DIRECTIVE.match(line.strip())
            return found.group(1) if found else None
    return None


def runtime_edges(code: str, executable: str) -> list[Edge]:
    """Every import that reaches the bundle. A type-only import is erased by the compiler."""
    def edge(match: re.Match, spec: str | None) -> Edge:
        first = code.count("\n", 0, match.start()) + 1
        return Edge(first, first + match.group(0).count("\n"), spec)

    def named(match: re.Match) -> str:
        return code[match.start("spec"):match.end("spec")]

    edges = [edge(m, named(m)) for m in FROM.finditer(executable) if not type_only(m.group("clause"))]
    edges += [edge(m, named(m)) for m in BARE.finditer(executable)]
    # A call given anything but a plain string literal, a variable or a ${...} template, names no module.
    edges += [edge(m, None if m.group("other") else named(m)) for m in CALL.finditer(executable)]
    return edges


def type_only(clause: str) -> bool:
    clause = clause.strip()
    if re.match(r"type\s*[{*\w]", clause) and clause != "type":
        return True
    names = re.fullmatch(r"\{(.*)\}", clause, re.S)
    parts = [p.strip() for p in names.group(1).split(",") if p.strip()] if names else []
    return bool(parts) and all(p.startswith("type ") for p in parts)


class Graph:
    """The project's modules as the bundler sees them, read once per process."""

    def __init__(self, root: Path):
        self.root = root
        self.modules: dict[Path, Module | None] = {}
        self.configs: dict[Path, tuple | None] = {}
        self.importers: dict[str, list[Path]] | None = None

    def module(self, path: Path) -> Module | None:
        """A module's directive and runtime edges, or None when no reader can be trusted with it."""
        if path not in self.modules:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                reading = read(text, path.suffix)
            except (OSError, Unparsable):
                self.modules[path] = None
            else:
                code = blank(text, [(s, e) for s, e, _ in reading.comments])
                executable = blank(code, reading.literals)
                self.modules[path] = Module(directive(code.split("\n")), runtime_edges(code, executable))
        return self.modules[path]

    def resolve(self, spec: str, origin: Path):
        """("file", path), ("package", name) or ("unknown", spec)."""
        for base in self.local_candidates(spec, origin):
            found = file_for(base)
            if found:
                return "file", found.resolve()
        # A path alias that names nothing is unknown. A bare name that baseUrl does not hold is a package,
        # as it is to TypeScript, which looks there first and in node_modules after.
        paths, _ = self.project(origin)
        if spec.startswith((".", "/", "@/", "~/", "#")) or any(alias_star(p, spec) is not None for p, _, _ in paths):
            return "unknown", spec
        return "package", spec

    def local_candidates(self, spec: str, origin: Path) -> list[Path]:
        if spec.startswith("."):
            return [origin.parent / spec]
        paths, base_url = self.project(origin)
        candidates = []
        for pattern, targets, base in paths:
            star = alias_star(pattern, spec)
            if star is not None:
                candidates += [base / target.replace("*", star) for target in targets]
        if base_url is not None and not spec.startswith("/"):
            candidates.append(base_url / spec)
        return candidates

    def project(self, origin: Path) -> tuple[list, Path | None]:
        """paths and baseUrl from the nearest tsconfig.json or jsconfig.json."""
        folder = origin.parent
        while True:
            if folder not in self.configs:
                self.configs[folder] = next((project_settings(folder / name) for name in ("tsconfig.json", "jsconfig.json")
                                             if (folder / name).is_file()), None)
            if self.configs[folder] is not None or folder == self.root or folder == folder.parent:
                return self.configs[folder] or ([], None)
            folder = folder.parent


def alias_star(pattern: str, spec: str) -> str | None:
    if "*" not in pattern:
        return "" if spec == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
        return spec[len(prefix):len(spec) - len(suffix)]
    return None


def project_settings(config: Path, depth: int = 0) -> tuple[list, Path | None]:
    """compilerOptions.paths as (pattern, targets, base directory) and the baseUrl directory, each
    inherited through a relative "extends" when this file does not set it."""
    paths, base_url = [], None
    try:
        data = jsonc(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    parents = data.get("extends")
    for parent in ([parents] if isinstance(parents, str) else parents or []):
        if isinstance(parent, str) and parent.startswith(".") and depth < 5:
            target = config.parent / parent
            target = target if target.suffix == ".json" else target.with_name(target.name + ".json")
            inherited_paths, inherited_base = project_settings(target, depth + 1)
            paths, base_url = inherited_paths or paths, inherited_base or base_url
    compiler = data.get("compilerOptions") or {}
    if isinstance(compiler.get("baseUrl"), str):
        base_url = config.parent / compiler["baseUrl"]
    if isinstance(compiler.get("paths"), dict):
        base = base_url or config.parent
        paths = [(pattern, [t for t in targets if isinstance(t, str)], base)
                 for pattern, targets in compiler["paths"].items() if isinstance(targets, list)]
    return paths, base_url


def jsonc(text: str) -> dict:
    """JSON that allows comments and trailing commas, as tsconfig.json does."""
    reading = read(text, ".js")
    plain = blank(text, [(s, e) for s, e, _ in reading.comments])
    data = json.loads(re.sub(r",(\s*[}\]])", r"\1", plain))
    return data if isinstance(data, dict) else {}


def file_for(base: Path) -> Path | None:
    """The file an import names: as written, with a source suffix, as a directory index, or .js for .ts."""
    if base.is_file():
        return base
    for suffix in SUFFIXES:
        if Path(f"{base}{suffix}").is_file():
            return Path(f"{base}{suffix}")
    for suffix in SUFFIXES:
        if (base / f"index{suffix}").is_file():
            return base / f"index{suffix}"
    if base.suffix in (".js", ".jsx", ".mjs", ".cjs"):
        for suffix in (".ts", ".tsx", ".mts", ".cts"):
            if base.with_suffix(suffix).is_file():
                return base.with_suffix(suffix)
    return None


def server_only(spec: str, origin: Path, root: Path) -> bool:
    if spec in SERVER_MARKERS:
        return True
    name = spec[5:] if spec.startswith("node:") else spec
    if name.split("/")[0] in SERVER_BUILTINS:
        return True
    package = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
    return package in NODE_PACKAGES and not browser_build(package, origin, root)


def browser_build(package: str, origin: Path, root: Path) -> bool:
    """True when the installed package names a file for browsers in place of its main entry."""
    folder = origin.parent
    while True:
        manifest = folder / "node_modules" / package / "package.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            return isinstance(data, dict) and names_browser_entry(data)
        if folder == root or folder == folder.parent:
            return False
        folder = folder.parent


def names_browser_entry(manifest: dict) -> bool:
    """A browser string, a browser map that replaces the main entry with a file, or a browser export."""
    browser = manifest.get("browser")
    if isinstance(browser, str) and browser:
        return True
    if isinstance(browser, dict):
        main = entry_key(manifest.get("main") or "index.js")
        return any(entry_key(key) == main and isinstance(value, str) and value for key, value in browser.items())
    exports = manifest.get("exports")
    root_export = exports.get(".", exports) if isinstance(exports, dict) else None
    return isinstance(root_export, dict) and isinstance(root_export.get("browser"), str)


def entry_key(path: str) -> str:
    path = path[2:] if path.startswith("./") else path
    return path if Path(path).suffix else path + ".js"


def reaches_server(graph: Graph, edge: Edge, origin: Path) -> str | None:
    """What server-only module this edge leads to, or None. Raises Incomplete when that cannot be known."""
    queue, seen = [(edge, origin)], set()
    while queue:
        current, source = queue.pop(0)
        if current.spec is None:
            raise Incomplete(f"line {current.first} imports a computed path")
        kind, target = graph.resolve(current.spec, source)
        if kind == "unknown":
            raise Incomplete(f"{target} does not resolve to a file")
        if kind == "package":
            if server_only(target, source, graph.root):
                return target
            continue
        if target in seen or target.suffix not in SUFFIXES:
            continue
        seen.add(target)
        if len(seen) > MAX_MODULES:
            raise Incomplete(f"the import graph passes {MAX_MODULES} modules")
        module = graph.module(target)
        if module is None:
            raise Incomplete(f"{target.name} cannot be read")
        if module.directive == "server":
            continue
        queue += [(e, target) for e in module.edges]
    return None


def client_reachable(graph: Graph, path: Path) -> bool:
    """Whether some "use client" module imports this one at runtime, through anything but a server module."""
    index = specifier_index(graph)
    queue, seen = [path], {path}
    while queue:
        target = queue.pop(0)
        importers = sorted({f for name in module_names(str(target)) for f in index.get(name, [])})
        for importer in importers:
            if importer in seen:
                continue
            module = graph.module(importer)
            if module is None:
                raise Incomplete(f"{importer.name} may import it and cannot be read")
            if not any(e.spec and graph.resolve(e.spec, importer) == ("file", target) for e in module.edges):
                continue
            if module.directive == "client":
                return True
            if module.directive != "server":
                seen.add(importer)
                queue.append(importer)
    return False


def module_names(path: str) -> set[str]:
    """The names a module is imported by: its last segment without a source suffix, and for an index
    file its directory too. Used alike for a file and for a specifier, so the two always meet."""
    parts = [p for p in re.sub(r"\.[cm]?[jt]sx?$", "", path).split("/") if p]
    return set(parts[-1:]) | ({parts[-2]} if len(parts) > 1 and parts[-1] == "index" else set())


def specifier_index(graph: Graph) -> dict[str, list[Path]]:
    """Every project file under each name its quoted text could import. A file can only import a module
    by one of the module's names, so only those files are parsed, and resolution then decides."""
    if graph.importers is None:
        files = []
        for folder, subfolders, names in os.walk(graph.root):
            subfolders[:] = sorted(d for d in subfolders if d not in SKIPPED_DIRECTORIES and not d.startswith("."))
            files += [Path(folder).resolve() / n for n in sorted(names) if Path(n).suffix in JS_LIKE]
            if len(files) > MAX_PROJECT_FILES:
                raise Incomplete(f"the project holds more than {MAX_PROJECT_FILES} modules")
        graph.importers = {}
        for file in files:
            text = file.read_text(encoding="utf-8", errors="replace")
            for name in {n for quoted in QUOTED_PATH.findall(text) for n in module_names(quoted)}:
                graph.importers.setdefault(name, []).append(file)
    return graph.importers


GRAPHS: dict[Path, Graph] = {}


@rule("client-bundles-server-code", "client module reaches a server-only package",
      "A browser bundle cannot contain this. Import the type, or move the value behind the server boundary.",
      languages=frozenset(JS_LIKE))
def _client_bundles_server_code(src: Source, cfg: Config, root: Path) -> list[str]:
    here = directive(src.code_lines)
    if here == "server":
        return []
    graph = GRAPHS.setdefault(root.resolve(), Graph(root.resolve()))
    path = src.path.resolve()
    edges = runtime_edges("\n".join(src.code_lines), "\n".join(src.executable_lines))
    edited = src.edited
    directive_edited = here == "client" and edited is not None and first_code_line(src) in edited
    lines = []
    for edge in edges:
        touched = [n for n in range(edge.first, edge.last + 1) if edited is None or n in edited]
        if not (touched or directive_edited):
            continue
        try:
            reached = reaches_server(graph, edge, path)
        except Incomplete:
            # An unknown path matters only where a client bundle can include this module.
            if here == "client" or client_reachable(graph, path):
                raise
            continue
        if reached is None:
            continue
        # A module below a client puts its edge in a bundle only when some client reaches it.
        if here != "client" and not client_reachable(graph, path):
            return []
        lines.append(touched[0] if touched else first_code_line(src))
    return [hit(src, n) for n in sorted(set(lines))]


def first_code_line(src: Source) -> int:
    return next((i + 1 for i, line in enumerate(src.code_lines) if line.strip()), 1)
