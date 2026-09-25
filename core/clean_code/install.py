"""Installing and removing the hooks, the skills and the commands for each agent, all or nothing.

Every settings file is read and checked before anything changes. The new state is staged beside the
old one, swapped in by rename and swapped back if any step fails. Settings are merged, never replaced.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Grammars that carry their compiled code, so a hook never downloads one. tree-sitter 0.23 is the last
# release for Python 3.9.
PARSER_PACKAGES = [
    'tree-sitter==0.23.2; python_version < "3.10"',
    'tree-sitter==0.26.0; python_version >= "3.10"',
    'tree-sitter-javascript==0.23.1; python_version < "3.10"',
    'tree-sitter-javascript==0.25.0; python_version >= "3.10"',
    'tree-sitter-python==0.23.6; python_version < "3.10"',
    'tree-sitter-python==0.25.0; python_version >= "3.10"',
    "tree-sitter-typescript==0.23.2",
]

# Earlier settings files kept beside each one, newest first.
KEPT_BACKUPS = 3

# Written into the core when the parser is off, so the hooks never load one the interpreter happens to hold.
PARSER_OFF = "parser-off"

# The modes a clean-code hook command has ever ended with, Stop included.
HOOK_MODES = {"pre", "post", "stop"}

STAGED = ".clean-code-new"
SET_ASIDE = ".clean-code-old"


class InstallError(Exception):
    """A problem found before or during the change. Nothing is left changed when it is raised."""


@dataclass(frozen=True)
class Agent:
    name: str
    marker: Path
    settings: Path
    pre_matcher: str
    post_matcher: str
    failure_matcher: str | None
    """Tools whose failed runs still get a post check, for agents that report those on their own event."""
    placements: tuple[tuple[str, Path], ...]
    next_step: str


def agents(home: Path) -> dict[str, Agent]:
    return {
        "claude": Agent(
            name="Claude Code",
            marker=home / ".claude",
            settings=home / ".claude/settings.json",
            pre_matcher="Edit|Write|Bash|PowerShell",
            post_matcher="Edit|Write|Bash|PowerShell",
            failure_matcher="Bash|PowerShell",
            placements=(
                ("skills/clean-code", home / ".claude/skills/clean-code"),
                ("claude/commands/clean.md", home / ".claude/commands/clean.md"),
                ("claude/commands/options.md", home / ".claude/commands/options.md"),
                ("claude/commands/clean-config.md", home / ".claude/commands/clean-config.md"),
                ("claude/agents/clean-reviewer.md", home / ".claude/agents/clean-reviewer.md"),
            ),
            next_step="Restart it, then check /hooks.",
        ),
        "codex": Agent(
            name="Codex",
            marker=home / ".codex",
            settings=home / ".codex/hooks.json",
            pre_matcher="Bash|apply_patch",
            post_matcher="Bash|apply_patch",
            failure_matcher=None,
            placements=(
                ("skills/clean-code", home / ".agents/skills/clean-code"),
                ("skills/clean-options", home / ".agents/skills/clean-options"),
                ("skills/clean-sweep", home / ".agents/skills/clean-sweep"),
            ),
            next_step="Open it, run /hooks, review the two clean_check hooks and trust them.",
        ),
    }


def main(argv: list[str], home: Path | None = None) -> int:
    home = home or Path.home()
    source = Path(__file__).resolve().parent.parent.parent
    try:
        known = {"--uninstall", "--no-parser"} | {"--" + key for key in agents(home)}
        unknown = [a for a in argv if a not in known]
        if unknown:
            raise InstallError(f"unknown option {unknown[0]}.")
        found = agents(home)
        named = [key for key in found if "--" + key in argv] or [k for k, a in found.items() if a.marker.is_dir()]
        if not named:
            raise InstallError("no agent found. Pass --claude or --codex.")
        if "--uninstall" in argv:
            lines = uninstall(home, [found[k] for k in named], list(found.values()))
        else:
            set_up_parser = without_parser if "--no-parser" in argv else parser_environment
            lines = install(home, source, [found[k] for k in named], set_up_parser)
    except InstallError as exc:
        print(f"clean-code: {exc} Nothing was changed.", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


def install(home: Path, source: Path, chosen: list[Agent], set_up_parser: Callable[[Path], str]) -> list[str]:
    core = home / ".clean-code"
    # The core is shared, so every agent whose hooks already run it moves to the new interpreter too.
    others = [a for a in agents(home).values() if a not in chosen]
    rewired = chosen + [a for a in others if still_uses(a, core / "clean_check.py")]
    finals = [core] + [target for agent in chosen for _, target in agent.placements] + [a.settings for a in rewired]
    recover(finals)
    for agent in rewired:
        read_settings(agent.settings)
    staged_core = core.with_name(core.name + STAGED)
    remove(staged_core)
    shutil.copytree(source / "core/clean_code", staged_core / "clean_code",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(source / "core/clean_check.py", staged_core / "clean_check.py")
    parser = set_up_parser(staged_core)
    if parser != "on":
        (staged_core / PARSER_OFF).write_text(parser + "\n", encoding="utf-8")
    python = core / "venv/bin/python" if (staged_core / "venv").is_dir() else Path(sys.executable)
    swaps = [(core, staged_core)]
    try:
        for agent in chosen:
            swaps += [(target, stage_copy(source / relative, target)) for relative, target in agent.placements]
        for agent in rewired:
            swaps.append((agent.settings, stage_text(agent.settings, merged_settings(agent, core / "clean_check.py", python))))
        commit(swaps)
    finally:
        for _, staged in swaps:
            remove(staged)
    status = "on, tree-sitter in " + str(core / "venv") if parser == "on" else f"OFF, {parser}"
    lines = [f"{agent.name}: installed. {agent.next_step}" for agent in chosen]
    lines.append(f"React, TSX and JSX checking: {status}.")
    if parser != "on":
        lines.append("Every .tsx and .jsx edit will be reported as not checked until install runs again with a parser.")
    return lines


def uninstall(home: Path, chosen: list[Agent], every: list[Agent]) -> list[str]:
    core = home / ".clean-code"
    check = core / "clean_check.py"
    finals = [a.settings for a in chosen] + [target for agent in chosen for _, target in agent.placements] + [core]
    recover(finals)
    for agent in chosen:
        read_settings(agent.settings)
    # Hooks go first, so no hook is ever left pointing at a core that is gone.
    swaps = [(agent.settings, stage_text(agent.settings, merged_settings(agent, check, None))) for agent in chosen]
    swaps += [(target, None) for agent in chosen for _, target in agent.placements]
    if not any(still_uses(agent, check) for agent in every if agent not in chosen):
        swaps.append((core, None))
    try:
        commit(swaps)
    finally:
        for _, staged in swaps:
            remove(staged)
    return [f"{agent.name}: removed." for agent in chosen]


def without_parser(core: Path) -> str:
    return "not requested (--no-parser)"


def parser_environment(core: Path) -> str:
    """Build a private Python with the grammars, and prove it parses TSX. "on", or why it could not."""
    venv = core / "venv"
    python = venv / "bin/python"
    steps = [
        [sys.executable, "-m", "venv", str(venv)],
        [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *PARSER_PACKAGES],
        [str(python), "-c", "import sys; sys.path.insert(0, sys.argv[1]); from clean_code import source; "
                            "assert source.parsed_reading('export const A = () => <p/>;', 'tsx')", str(core)],
    ]
    for step in steps:
        try:
            done = subprocess.run(step, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            remove(venv)
            return f"could not set up tree-sitter ({exc})"
        if done.returncode != 0:
            remove(venv)
            last = (done.stderr.strip().splitlines() or ["no output"])[-1]
            return f"could not set up tree-sitter ({last[:160]})"
    return "on"


def merged_settings(agent: Agent, check: Path, python: Path | None) -> str:
    """The agent's settings with every earlier clean-code hook removed, and ours added unless removing."""
    data = read_settings(agent.settings)
    hooks = data.get("hooks", {})
    # Every event, so hooks from earlier versions such as Stop go too; anyone else's entries stay.
    for event in list(hooks):
        hooks[event] = [g for g in (without_ours(group, check) for group in hooks[event]) if g is not None]
    if python is not None:
        events = [("PreToolUse", agent.pre_matcher, "pre", 10), ("PostToolUse", agent.post_matcher, "post", 15)]
        if agent.failure_matcher:
            events.append(("PostToolUseFailure", agent.failure_matcher, "post", 15))
        for event, matcher, mode, timeout in events:
            command = f'"{python}" "{check}" {mode}'
            hooks.setdefault(event, []).append({"matcher": matcher, "hooks": [{"type": "command", "command": command,
                                                                            "timeout": timeout}]})
    data["hooks"] = {event: groups for event, groups in hooks.items() if groups}
    if not data["hooks"]:
        del data["hooks"]
    return json.dumps(data, indent=2) + "\n"


def read_settings(file: Path) -> dict:
    try:
        text = file.read_text(encoding="utf-8") if file.is_file() else ""
    except OSError as exc:
        raise InstallError(f"cannot read {file}: {exc.strerror}.") from exc
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise InstallError(f"{file} is not valid JSON (line {exc.lineno}): fix it first.") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{file} does not hold a JSON object.")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f'{file} has a "hooks" value that is not an object: fix it first.')
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise InstallError(f'{file} has a "hooks.{event}" value that is not a list: fix it first.')
    return data


def runs_checker(command, check: Path) -> bool:
    """A command clean-code wrote: an interpreter, this checker, a hook mode. A mere mention is not one."""
    try:
        words = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return False
    return (len(words) == 3 and Path(words[0]).name.startswith("python") and words[1] == str(check)
            and words[2] in HOOK_MODES)


def is_ours(hook, check: Path) -> bool:
    return isinstance(hook, dict) and runs_checker(hook.get("command"), check)


def without_ours(group, check: Path):
    """The group with clean-code's hooks taken out, None once nothing else is left in it."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return group
    kept = [h for h in group["hooks"] if not is_ours(h, check)]
    if len(kept) == len(group["hooks"]):
        return group
    return {**group, "hooks": kept} if kept else None


def group_is_ours(group, check: Path) -> bool:
    return isinstance(group, dict) and isinstance(group.get("hooks"), list) and any(is_ours(h, check) for h in group["hooks"])


def still_uses(agent: Agent, check: Path) -> bool:
    """Whether another agent's hooks still run the core. Settings that cannot be read are taken to."""
    try:
        hooks = read_settings(agent.settings).get("hooks", {})
    except InstallError:
        return True
    return isinstance(hooks, dict) and any(group_is_ours(g, check) for groups in hooks.values()
                                           if isinstance(groups, list) for g in groups)


def stage_copy(source: Path, target: Path) -> Path:
    staged = target.with_name(target.name + STAGED)
    remove(staged)
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, staged) if source.is_dir() else shutil.copy2(source, staged)
    except OSError as exc:
        raise InstallError(f"cannot write beside {target}: {exc.strerror}.") from exc
    return staged


def stage_text(target: Path, text: str) -> Path:
    staged = target.with_name(target.name + STAGED)
    try:
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"cannot write beside {target}: {exc.strerror}.") from exc
    return staged


def commit(swaps: list[tuple[Path, Path | None]]) -> None:
    """Swap each staged path in for its final one, or take the final away when nothing is staged.

    Each final is renamed aside before its replacement is renamed in. On any failure every swap made so
    far is undone, so the old installation stands exactly as it was.
    """
    done: list[tuple[Path, Path | None]] = []
    try:
        for final, staged in swaps:
            aside = final.with_name(final.name + SET_ASIDE) if final.exists() or final.is_symlink() else None
            if aside:
                remove(aside)
                os.replace(final, aside)
            done.append((final, aside))
            if staged is not None:
                os.replace(staged, final)
    except OSError as exc:
        for final, aside in reversed(done):
            remove(final)
            if aside:
                os.replace(aside, final)
        raise InstallError(f"could not replace {exc.filename or 'a file'}: {exc.strerror}; the old state is back.") from exc
    for final, aside in done:
        if aside and final.suffix == ".json":
            keep_backup(final, aside)
        elif aside:
            remove(aside)


def keep_backup(final: Path, aside: Path) -> None:
    """Keep the previous settings under a dated name, and only the newest few of those."""
    os.replace(aside, final.with_name(f"{final.name}.clean-code-{time.time_ns()}.bak"))
    backups = sorted(final.parent.glob(f"{final.name}.clean-code-*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[KEPT_BACKUPS:]:
        old.unlink()


def recover(finals: list[Path]) -> None:
    """Undo what an interrupted run left: staged copies go, and anything set aside goes back in place."""
    for final in finals:
        remove(final.with_name(final.name + STAGED))
        aside = final.with_name(final.name + SET_ASIDE)
        if aside.exists():
            remove(final)
            os.replace(aside, final)


def remove(path: Path | None) -> None:
    if path is None:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()
