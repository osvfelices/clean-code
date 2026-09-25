"""What a shell command did to a Git worktree, seen between its pre and post hooks.

The pre hook records the worktree, copying only files Git does not already hold; the post hook compares.
Whatever cannot be compared exactly is reported as not checked, never as clean.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import CHECKED, Config, matches_any
from .hooks import test_deletion, weakening

SHELLS = {"Bash", "PowerShell"}

# How long a command may hold its snapshot after its pre hook. Claude Code ends a shell tool call at its
# timeout, though it may leave the process running in the background, where later writes are outside the
# observation boundary anyway. Codex's hook input carries no timeout and an exec session can be resumed
# with write_stdin, so Codex gets a fixed lease. The grace covers a permission prompt answered late.
CLAUDE_TIMEOUT_MS, CLAUDE_TIMEOUT_MAX_MS = 120_000, 600_000
CODEX_LEASE, GRACE = 30 * 60, 5 * 60
# An expired record leaves only a tombstone, so a post arriving after it is told why it was not checked.
TOMBSTONE_TTL, TOMBSTONE_LIMIT = 24 * 3600, 1000
# Bytes copied per file, per record and across all records. A file past them is kept as a SHA-256 digest,
# so a change to it is seen but cannot be diffed.
FILE_CAP, RECORD_CAP, STATE_CAP = 2 << 20, 64 << 20, 256 << 20
RECORD_LIMIT = 64
# Claimed, half-written and unrecognized entries left by a hook that was killed.
ABANDONED = 600
GIT_TIMEOUT = 8
RECORD_NAME = re.compile(r"^([0-9a-f]{16})\.(\d+)\.(\d+)\.([0-9a-f]{32})$")
BLOB_NAME = re.compile(r"^\d{1,9}$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
DIGEST = re.compile(r"^([0-9a-f]{64})?$")
INNER_SIGNATURE = re.compile(r"^(unreadable|[0-9a-f]{64})$")
MANIFEST_VERSION = 1


class Unsupported(Exception):
    """Why a command's changes cannot be compared exactly."""


def is_shell(payload: dict) -> bool:
    inp = payload.get("tool_input")
    command = inp.get("command") if isinstance(inp, dict) else None
    return payload.get("tool_name") in SHELLS and not (isinstance(command, str) and "*** Begin Patch" in command)


def record_key(payload: dict) -> str | None:
    """The invocation's identity, the same at its pre and at its post. Never a PID or the command text."""
    tool_use = payload.get("tool_use_id")
    if not isinstance(tool_use, str) or not tool_use:
        return None
    return hashlib.sha256(json.dumps(invocation(payload)).encode()).hexdigest()[:32]


def invocation(payload: dict) -> list:
    agent = "codex" if "turn_id" in payload else "claude"
    return [agent, payload.get("session_id"), payload.get("agent_id"), payload.get("turn_id"), payload.get("tool_use_id")]


def lease(payload: dict, started: int) -> int:
    """When the command can no longer be running, from the timeout its tool declares."""
    if "turn_id" in payload:
        return started + CODEX_LEASE + GRACE
    timeout = (payload.get("tool_input") or {}).get("timeout")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        timeout = CLAUDE_TIMEOUT_MS
    return started + -(-min(timeout, CLAUDE_TIMEOUT_MAX_MS) // 1000) + GRACE


def state_dir() -> Path:
    """This user's record directory: created 0700, refused when it is a link or someone else owns it."""
    path = Path(tempfile.gettempdir()) / "clean-code-snapshots"
    path.mkdir(mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or not owned(info):
        raise Unsupported(f"{path} is not a private directory, so no snapshot was kept")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def owned(info: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or info.st_uid == os.getuid()


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, timeout=GIT_TIMEOUT,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unsupported(f"git could not be run ({exc})") from exc
    if done.returncode != 0:
        said = done.stderr.decode(errors="replace").strip().splitlines() or ["no output"]
        raise Unsupported(f"git failed ({said[-1][:160]})")
    return done.stdout


def worktree_root(cwd: Path) -> Path:
    try:
        top = git(cwd, "rev-parse", "--show-toplevel").decode().strip()
    except Unsupported as exc:
        raise Unsupported("the command ran outside a Git worktree, where no exact baseline is kept") from exc
    if not top:
        raise Unsupported("the command ran outside a Git worktree, where no exact baseline is kept")
    return Path(top)


@dataclass
class Worktree:
    index: dict[str, tuple[str, str]]
    """Stage-0 index entries: path to (mode, blob id)."""
    changed: set[str]
    """Tracked paths whose worktree differs from the index, deletions and conflicts included."""
    untracked: set[str]
    """Untracked paths Git shows; ignored paths are not among them."""
    inner: set[str]
    """Submodules and nested repositories: their files are not this worktree's to compare."""


def read_worktree(root: Path) -> Worktree:
    index: dict[str, tuple[str, str]] = {}
    changed: set[str] = set()
    for entry in git(root, "ls-files", "-s", "-z").split(b"\0"):
        if entry:
            meta, _, name = entry.partition(b"\t")
            mode, oid, stage = meta.decode().split()
            if stage == "0":
                index[os.fsdecode(name)] = (mode, oid)
            else:
                changed.add(os.fsdecode(name))
    untracked: set[str] = set()
    inner = {path for path, (mode, _) in index.items() if mode == "160000"}
    status = git(root, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-renames", "--ignore-submodules=none")
    for entry in status.split(b"\0"):
        if entry[:2] == b"? ":
            path = os.fsdecode(entry[2:])
            # Git lists a repository nested inside the worktree as one directory.
            (inner if path.endswith("/") else untracked).add(path.rstrip("/"))
        elif entry[:2] == b"1 " and entry.split(b" ", 2)[1][1:2] != b".":
            changed.add(os.fsdecode(entry.split(b" ", 8)[8]))
        elif entry[:2] == b"u ":
            changed.add(os.fsdecode(entry.split(b" ", 10)[10]))
    return Worktree(index, changed - inner, untracked, inner)


def lstat(path: str | Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def identity(info: os.stat_result) -> list:
    """What survives a rename and only a rename: the inode, and its birth time where the platform keeps one."""
    return [info.st_dev, info.st_ino, getattr(info, "st_birthtime", None)]


def digest(path: Path) -> str | None:
    """SHA-256 of a regular file read in chunks, never through a link; None when it cannot be read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    sha = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                sha.update(chunk)
    except OSError:
        return None
    return sha.hexdigest()


def outside_link(root: Path, path: str) -> list | None:
    """For a link that leads out of the worktree, its target's size and time; None for any other path.

    The target is never read. A link into the worktree needs nothing: its target is compared in place.
    """
    full = root / path
    if not full.is_symlink():
        return None
    target = Path(os.path.realpath(full))
    if target == root or root in target.parents:
        return None
    info = lstat(target)
    return [info.st_size, info.st_mtime_ns, info.st_ino] if info else ["missing"]


def inner_signature(repo: Path) -> str:
    """Enough of a nested repository's state to see that it changed, without comparing it."""
    try:
        tree = read_worktree(repo)
    except Unsupported:
        return "unreadable"
    touched = {p: digest(repo / p) for p in sorted(tree.changed | tree.untracked)}
    return hashlib.sha256(json.dumps([sorted(tree.index.items()), touched]).encode()).hexdigest()


def baseline(root: Path, blobs: Path, budget: int) -> dict:
    """The worktree as the command will find it.

    Clean tracked files are named by blob, other files are copied, or kept as a digest past the caps.
    Every regular file's identity is kept too, the only proof a new path is an old file renamed.
    """
    tree = read_worktree(root)
    kept: dict[str, str] = {}
    digests: dict[str, str] = {}
    ids: dict[str, list] = {}
    links: dict[str, list | None] = {}
    # Tracked files first: their changes are the likeliest to need a diff.
    for path in sorted(tree.changed) + sorted(tree.untracked - tree.changed):
        info = lstat(root / path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            links[path] = outside_link(root, path)
        if not stat.S_ISREG(info.st_mode):
            continue
        ids[path] = identity(info)
        name = str(len(kept))
        try:
            if info.st_size > min(FILE_CAP, budget):
                raise OSError("over the cap")
            (blobs / name).write_bytes((root / path).read_bytes())
        except OSError:
            digests[path] = digest(root / path) or ""
            continue
        kept[path] = name
        budget -= info.st_size
    clean: dict[str, str] = {}
    for path, (mode, oid) in tree.index.items():
        if path in tree.changed:
            continue
        if mode == "120000":
            links[path] = outside_link(root, path)
        info = lstat(root / path) if mode.startswith("100") else None
        if info is not None and stat.S_ISREG(info.st_mode):
            clean[path] = oid
            ids[path] = identity(info)
    return {"clean": clean, "kept": kept, "digests": digests, "ids": ids, "links": links,
            "inner": {path: inner_signature(root / path) for path in tree.inner}}


def before(payload: dict) -> None:
    """Record the worktree a shell command is about to change, under the command's own identity.

    Whatever goes wrong here is left for the post to report: with no usable record it says not checked.
    """
    key = record_key(payload)
    if key is None:
        return
    state = state_dir()
    live = sweep(state)
    started = int(time.time())
    bound = {"version": MANIFEST_VERSION, "key": key, "invocation": invocation(payload)}
    manifest: dict = {**bound, "background": (payload.get("tool_input") or {}).get("run_in_background") is True}
    staging = Path(tempfile.mkdtemp(prefix=".tmp.", dir=state))
    (staging / "blobs").mkdir()
    tag = "0" * 16
    try:
        if len(live) >= RECORD_LIMIT:
            raise Unsupported("too many shell commands were being tracked at once")
        root = worktree_root(Path(payload.get("cwd") or os.getcwd()))
        budget = min(RECORD_CAP, STATE_CAP - sum(size(p) for p in live))
        manifest.update(root=str(root), **baseline(root, staging / "blobs", budget))
        tag = root_tag(root)
    except Unsupported as exc:
        manifest = {**bound, "unsupported": str(exc)}
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    os.rename(staging, state / f"{tag}.{started}.{lease(payload, started)}.{key}")


def changes(payload: dict) -> tuple[Path, list[Change], list[str]]:
    """Claim this command's record and compare the worktree with it: its root, what changed, and doubts about it all.

    Raises Unsupported when nothing about the command can be checked.
    """
    key = record_key(payload)
    if key is None:
        raise Unsupported("the hook input has no tool_use_id to pair it with a snapshot")
    state = state_dir()
    sweep(state)
    claimed, tag, until = claim(state, key)
    try:
        record = load_record(claimed, payload, key, tag)
        if until < time.time():
            raise Unsupported("its pre-state expired before the command finished")
        found = compare(record)
        # Of two overlapping commands, the first to finish finds the other's record and marks it.
        others = concurrent(state, tag, key)
        for other in others:
            create(state, f".shared.{other}")
        if (others or lstat(state / f".shared.{key}")) and found:
            raise Unsupported("another shell command was changing this worktree at the same time, so its changes cannot be told apart")
    finally:
        remove(claimed)
        remove(state / f".shared.{key}")
    doubts = ["it runs in the background, and what it writes after this hook is not seen"] if record.background else []
    return record.root, found, doubts


@dataclass
class Record:
    """A snapshot that was proven to belong to this command and this worktree, every path and blob checked."""
    root: Path
    clean: dict[str, str]
    kept: dict[str, Path]
    digests: dict[str, str]
    ids: dict[str, list]
    links: dict[str, list | None]
    inner: dict[str, str]
    background: bool


def load_record(claimed: Path, payload: dict, key: str, tag: str) -> Record:
    """The claimed record, trusted only after it matches what the hook input says independently.

    The worktree comes from the payload's cwd, never from the record. Nothing in the project, and no saved
    copy, is read before every field has been checked.
    """
    manifest = json.loads(read_private(claimed / "manifest.json"))
    if (not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION or manifest.get("key") != key
            or manifest.get("invocation") != invocation(payload)):
        raise Unsupported("its snapshot does not belong to this command")
    if "unsupported" in manifest:
        raise Unsupported(str(manifest["unsupported"])[:200])
    root = worktree_root(Path(payload.get("cwd") or os.getcwd()))
    if manifest.get("root") != str(root) or tag != root_tag(root):
        raise Unsupported("its snapshot was taken for another worktree")
    blobs = claimed / "blobs"
    # Each field's values in the exact shape baseline() writes, and what its path may be now: a file may
    # have been deleted, a link replaced by a file, a nested repository removed. Never a link to follow.
    files = {"absent", "file"}
    fields = {"clean": (spelled(OBJECT_ID), files), "kept": (spelled(BLOB_NAME), files),
              "digests": (spelled(DIGEST), files), "links": (is_link_signature, {"absent", "link", "file"}),
              "inner": (spelled(INNER_SIGNATURE), {"absent", "dir"})}
    safe_dirs: set[str] = set()
    for name, (valid, kinds) in fields.items():
        entries = manifest.get(name)
        if not isinstance(entries, dict) or not all(valid(v) and kind(root, p, safe_dirs) in kinds
                                                    for p, v in entries.items()):
            raise Unsupported(f"its snapshot is damaged ({name})")
    ids = manifest.get("ids")
    regular = manifest["clean"].keys() | manifest["kept"].keys() | manifest["digests"].keys()
    if not isinstance(ids, dict) or ids.keys() != regular or not all(map(is_identity, ids.values())):
        raise Unsupported("its snapshot is damaged (ids)")
    if not is_dir(blobs) or not all((info := lstat(blobs / name)) and stat.S_ISREG(info.st_mode)
                                    for name in manifest["kept"].values()):
        raise Unsupported("its snapshot is damaged (kept)")
    if not isinstance(manifest.get("background"), bool):
        raise Unsupported("its snapshot is damaged (background)")
    return Record(root, manifest["clean"], {p: blobs / n for p, n in manifest["kept"].items()}, manifest["digests"],
                  manifest["ids"], manifest["links"], manifest["inner"], manifest["background"])


def spelled(pattern: re.Pattern):
    return lambda value: isinstance(value, str) and bool(pattern.match(value))


def counts(value, length: int) -> bool:
    """A list of exactly length non-negative ints, bools excluded: how stat numbers are stored."""
    return isinstance(value, list) and len(value) == length and all(type(v) is int and v >= 0 for v in value)


def is_identity(value) -> bool:
    """identity(): [st_dev, st_ino, st_birthtime], the last a float or None where the platform keeps none."""
    return isinstance(value, list) and len(value) == 3 and counts(value[:2], 2) and (value[2] is None or type(value[2]) is float)


def is_link_signature(value) -> bool:
    """outside_link(): None, ["missing"], or the target's [st_size, st_mtime_ns, st_ino].

    A timestamp before 1970 is negative, so only the size and the inode must be non-negative.
    """
    if value is None or value == ["missing"]:
        return True
    return (isinstance(value, list) and len(value) == 3 and all(type(v) is int for v in value)
            and value[0] >= 0 and value[2] >= 0)


def kind(root: Path, path, safe_dirs: set[str]) -> str | None:
    """What a path from state is now, told by lstat alone: "file", "dir", "link", "other" or "absent".

    None when the path could leave the worktree: not relative, climbing out, or through a link on the way.
    safe_dirs holds the directories already found to be real ones, so each is looked at once. Plain
    strings, not Path objects: this runs for every tracked file.
    """
    if not isinstance(path, str) or not path or "\0" in path or os.path.isabs(path):
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    probe = str(root)
    for part in parts[:-1]:
        probe += os.sep + part
        if probe in safe_dirs:
            continue
        info = lstat(probe)
        if info is not None and stat.S_ISDIR(info.st_mode):
            safe_dirs.add(probe)
        elif info is not None and stat.S_ISLNK(info.st_mode):
            return None
        else:
            # Nothing is below a missing directory or a file.
            return "absent"
    info = lstat(probe + os.sep + parts[-1])
    if info is None:
        return "absent"
    kinds = {stat.S_IFREG: "file", stat.S_IFDIR: "dir", stat.S_IFLNK: "link"}
    return kinds.get(stat.S_IFMT(info.st_mode), "other")


def is_dir(path: Path) -> bool:
    info = lstat(path)
    return info is not None and stat.S_ISDIR(info.st_mode)


def read_private(path: Path) -> bytes:
    """A file of this module's own state, opened without following a link."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Unsupported("its snapshot is damaged")
        return handle.read()


def create(state: Path, name: str) -> None:
    """Put an empty file at name, replacing whatever entry is there without ever following it."""
    fd, temporary = tempfile.mkstemp(prefix=".tmp.", dir=state)
    os.close(fd)
    try:
        os.replace(temporary, state / name)
    except OSError:
        os.unlink(temporary)


@dataclass
class Content:
    """A file's state at one hook: its bytes when small enough to hold, else its digest."""
    data: bytes | None
    sha: str | None
    ident: list | None = None

    def same_as(self, other: Content) -> bool | None:
        """Whether the two hold the same bytes; None when that cannot be established."""
        if self.data is not None and other.data is not None:
            return self.data == other.data
        mine = self.sha or (hashlib.sha256(self.data).hexdigest() if self.data is not None else None)
        theirs = other.sha or (hashlib.sha256(other.data).hexdigest() if other.data is not None else None)
        return None if not mine or not theirs else mine == theirs


@dataclass
class Change:
    path: str
    before: bytes | None
    """None when the file did not exist."""
    after: bytes | None
    """None when the file no longer exists."""
    doubt: str = ""
    """Why the change cannot be compared line by line."""


def compare(record: Record) -> list[Change]:
    root = record.root
    tree = read_worktree(root)
    clean, kept, digests, ids = record.clean, record.kept, record.digests, record.ids
    out: list[Change] = []
    for path, sig in record.links.items():
        info = lstat(root / path)
        if info is not None and not stat.S_ISLNK(info.st_mode):
            out.append(Change(path, None, None, "a link there was replaced by a file"))
        elif info is not None and outside_link(root, path) != sig:
            out.append(Change(path, None, None, "it is a link out of the worktree, whose target changed"))
    for path, sig in record.inner.items():
        if is_dir(root / path) and inner_signature(root / path) != sig:
            out.append(Change(path, None, None, "it is a nested repository or submodule, outside this worktree"))
    for path in tree.inner - record.inner.keys():
        out.append(Change(path, None, None, "it is a new nested repository, outside this worktree"))
    touched = [p for p, oid in clean.items() if p in tree.changed or tree.index.get(p, ("", ""))[1] != oid]
    touched += [*kept, *digests]
    known = clean.keys() | kept.keys() | digests.keys() | record.links.keys()
    # A link into the worktree that Git sees unchanged still points where it did.
    fixed_links = {p for p, (mode, _) in tree.index.items() if mode == "120000" and p not in tree.changed}
    fresh = sorted((tree.index.keys() | tree.untracked) - known - tree.inner - fixed_links)
    held = read_blobs(root, [clean[p] for p in touched if p in clean])
    gone: dict[str, Content] = {}
    stayed: dict[str, tuple[Content, Content]] = {}
    arrived: dict[str, Content] = {}
    for path in touched:
        if path in clean and clean[path] not in held:
            out.append(Change(path, None, None, "Git no longer holds its content from before the command"))
            continue
        data = held.get(clean[path]) if path in clean else read_private(kept[path]) if path in kept else None
        was = Content(data, digests.get(path), ids.get(path))
        now = observe(root, path)
        if isinstance(now, str):
            out.append(Change(path, None, None, now))
        elif now is None:
            gone[path] = was
        else:
            stayed[path] = (was, now)
    for path in fresh:
        if (root / path).is_symlink() and not outside_link(root, path):
            continue
        now = observe(root, path)
        if isinstance(now, str):
            out.append(Change(path, None, None, now))
        elif now is not None:
            arrived[path] = now
    return out + attributed(gone, stayed, arrived)


def observe(root: Path, path: str) -> Content | str | None:
    """The file as it is now, None when there is no regular file, or why its state cannot be known."""
    info = lstat(root / path)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "it is now a link out of the worktree" if outside_link(root, path) else "it is now a link"
    if not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size > FILE_CAP:
        sha = digest(root / path)
        return Content(None, sha, identity(info)) if sha else "it could not be read"
    try:
        return Content((root / path).read_bytes(), None, identity(info))
    except OSError:
        return "it could not be read"


def attributed(gone: dict[str, Content], stayed: dict[str, tuple[Content, Content]],
               arrived: dict[str, Content]) -> list[Change]:
    """Each changed path with the content it came from, proven by inode or by identical bytes, never by likeness.

    A file that now sits on a deleted file's inode is that file moved. A new file identical to exactly one
    deleted file, and it alone, is a pure rename. While any deleted file stays unpaired, a new file without
    proof may be that file renamed and rewritten, so it is not checked; only with no such deletion is it new.
    """
    moved = {tuple(content.ident): path for path, content in gone.items() if content.ident}
    out: list[Change] = []
    for path, (was, now) in stayed.items():
        origin = moved.get(tuple(now.ident)) if now.ident != was.ident else None
        if origin in gone:
            out.append(changed(path, gone.pop(origin), now))
        elif was.same_as(now) is not True:
            out.append(changed(path, was, now))
    unproven: dict[str, Content] = {}
    for path, now in arrived.items():
        origin = moved.get(tuple(now.ident))
        if origin in gone:
            out.append(changed(path, gone.pop(origin), now))
        else:
            unproven[path] = now
    twins = {path: [g for g, was in gone.items() if was.same_as(now)] for path, now in unproven.items()}
    counts = Counter(g for found in twins.values() for g in found)
    for path, now in unproven.items():
        if len(twins[path]) == 1 and counts[twins[path][0]] == 1:
            gone.pop(twins[path][0])
            out.append(Change(path, now.data or b"", now.data or b""))
        elif twins[path]:
            out.append(Change(path, None, None, "it is identical to more than one deleted file"))
    orphans = [path for path in unproven if not twins[path]]
    for path in orphans:
        if gone:
            out.append(Change(path, None, None, "a file deleted in the same command may have become it"))
        elif unproven[path].data is None:
            out.append(Change(path, None, None, "it is too large to compare"))
        else:
            out.append(Change(path, None, unproven[path].data))
    return out + [Change(path, was.data or b"", None) for path, was in gone.items()]


def changed(path: str, was: Content, now: Content) -> Change:
    same = was.same_as(now)
    if same:
        return Change(path, now.data or b"", now.data or b"")
    if same is None or was.data is None:
        return Change(path, None, None, "it was too large to keep a copy of before the command")
    if now.data is None:
        return Change(path, None, None, "it is too large to compare")
    return Change(path, was.data, now.data)


def read_blobs(root: Path, oids: list[str]) -> dict[str, bytes]:
    """Blob contents by id, from one git process. An id Git no longer has is left out."""
    if not oids:
        return {}
    out = git(root, "cat-file", "--batch", stdin="\n".join(oids).encode() + b"\n")
    found: dict[str, bytes] = {}
    at = 0
    while at < len(out):
        end = out.index(b"\n", at)
        head = out[at:end].split()
        at = end + 1
        if len(head) == 3:
            length = int(head[2])
            found[head[0].decode()] = out[at:at + length]
            at += length + 1
    return found


def claim(state: Path, key: str) -> tuple[Path, str, int]:
    """Take the record for this key by renaming it, so no other post can read it too; its root tag and lease."""
    for entry in os.listdir(state):
        parsed = RECORD_NAME.match(entry)
        if parsed and parsed.group(4) == key:
            taken = state / f".claimed.{key}.{secrets.token_hex(4)}"
            try:
                os.rename(state / entry, taken)
            except OSError:
                break
            if not is_dir(taken):
                remove(taken)
                raise Unsupported("its snapshot is damaged")
            return taken, parsed.group(1), int(parsed.group(3))
    tombstone = state / f".tomb.{key}"
    if lstat(tombstone):
        remove(tombstone)
        raise Unsupported("its pre-state expired before the command finished")
    raise Unsupported("no snapshot from before it ran was found (it was lost or never taken)")


def root_tag(root: Path) -> str:
    return hashlib.sha256(str(root).encode()).hexdigest()[:16]


def concurrent(state: Path, tag: str, key: str) -> list[str]:
    """Keys of other records for the same worktree whose commands may still be running."""
    now = time.time()
    return [m.group(4) for m in map(RECORD_NAME.match, os.listdir(state))
            if m and m.group(1) == tag and m.group(4) != key and int(m.group(3)) > now]


def sweep(state: Path) -> list[Path]:
    """Retire records whose lease ran out to tombstones, drop old leftovers; the records still live.

    The directory is private, but no entry in it is trusted: each is typed with lstat, never followed, and
    a directory this code did not name is left alone.
    """
    now = time.time()
    live: list[Path] = []
    tombstones: list[tuple[float, Path]] = []
    for entry in os.listdir(state):
        path = state / entry
        info = lstat(path)
        if info is None or not owned(info):
            continue
        record = RECORD_NAME.match(entry)
        if record and stat.S_ISDIR(info.st_mode) and int(record.group(3)) >= now:
            live.append(path)
        elif record:
            create(state, f".tomb.{record.group(4)}")
            remove(path)
        elif entry.startswith(".tomb."):
            tombstones.append((info.st_mtime, path))
        elif stat.S_ISDIR(info.st_mode) and not entry.startswith((".tmp.", ".claimed.")):
            continue
        elif info.st_mtime + (CODEX_LEASE + GRACE if entry.startswith(".shared.") else ABANDONED) < now:
            remove(path)
    tombstones.sort(reverse=True)
    for index, (mtime, path) in enumerate(tombstones):
        if index >= TOMBSTONE_LIMIT or mtime + TOMBSTONE_TTL < now:
            remove(path)
    return live


def remove(path: Path) -> None:
    """Delete a state entry; a link is removed itself, and rmtree never follows the links inside a directory."""
    info = lstat(path)
    if info is not None and stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path, ignore_errors=True)
    elif info is not None:
        path.unlink(missing_ok=True)


def size(record: Path) -> int:
    return sum(info.st_size for top, _, names in os.walk(record) for name in names
               if (info := lstat(Path(top) / name)) and stat.S_ISREG(info.st_mode))


def text_lines(data: bytes) -> list[str] | None:
    try:
        return [line.rstrip("\r") for line in data.decode("utf-8").split("\n")]
    except UnicodeDecodeError:
        return None


def diff(old: list[str], new: list[str]) -> tuple[set[int], list[tuple[str, str]]]:
    """The current line numbers a change added or replaced, and each hunk's removed and added text."""
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    middle = difflib.SequenceMatcher(None, old[head:len(old) - tail], new[head:len(new) - tail], autojunk=False)
    lines: set[int] = set()
    hunks: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in middle.get_opcodes():
        if tag != "equal":
            lines.update(range(head + j1 + 1, head + j2 + 1))
            hunks.append(("\n".join(old[head + i1:head + i2]), "\n".join(new[head + j1:head + j2])))
    return lines, hunks


def shell_report(payload: dict, cfg: Config, root: Path) -> tuple[list[tuple[Path, set[int]]], list[str], list[str]]:
    """Each file a shell command changed with the lines it introduced, what could not be checked, and weakened safeguards."""
    try:
        top, found, doubts = changes(payload)
    except (Unsupported, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return [], [f"shell command not checked, {exc}"], []
    scopes: list[tuple[Path, set[int]]] = []
    notes = [f"shell command not fully checked, {doubt}" for doubt in doubts]
    warnings: list[str] = []
    for change in found:
        path = top / change.path
        if matches_any(path, cfg.ignore, root):
            continue
        if change.doubt:
            notes.append(f"{change.path} not checked, {change.doubt}")
            continue
        lines: set[int] = set()
        hunks: list[tuple[str, str]] = []
        if change.after is not None:
            old = text_lines(change.before) if change.before is not None else []
            new = text_lines(change.after)
            if old is None or new is None:
                if path.suffix in CHECKED:
                    notes.append(f"{change.path} not checked, it is not UTF-8 text")
                continue
            lines, hunks = diff(old, new) if change.before is not None else (set(range(1, len(new) + 1)), [("", "\n".join(new))])
        why = weakening(path, hunks, root) or (test_deletion(path) if change.after is None else None)
        if why:
            warnings.append(f"the shell command {why}. It already ran: undo that change, or ask the user whether to keep it.")
        if lines and path.suffix in CHECKED:
            scopes.append((path, lines))
    return scopes, notes, warnings
