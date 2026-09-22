"""Installing and removing the hooks, the skills and the commands for each agent.

Settings files are merged, never replaced, and a .bak is left beside each one.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
CORE = HOME / ".clean-code"
CHECK = CORE / "clean_check.py"


@dataclass(frozen=True)
class Agent:
    name: str
    marker: Path
    settings: Path
    pre_matcher: str
    post_matcher: str
    placements: tuple[tuple[str, Path], ...]
    next_step: str

    def present(self) -> bool:
        return self.marker.is_dir()


AGENTS = {
    "claude": Agent(
        name="Claude Code",
        marker=HOME / ".claude",
        settings=HOME / ".claude/settings.json",
        pre_matcher="Edit|Write|MultiEdit|Bash",
        post_matcher="Edit|Write|MultiEdit",
        placements=(
            ("skills/clean-code", HOME / ".claude/skills/clean-code"),
            ("claude/commands/clean.md", HOME / ".claude/commands/clean.md"),
            ("claude/commands/options.md", HOME / ".claude/commands/options.md"),
            ("claude/commands/clean-config.md", HOME / ".claude/commands/clean-config.md"),
            ("claude/agents/clean-reviewer.md", HOME / ".claude/agents/clean-reviewer.md"),
        ),
        next_step="Restart it, then check /hooks.",
    ),
    "codex": Agent(
        name="Codex",
        marker=HOME / ".codex",
        settings=HOME / ".codex/hooks.json",
        pre_matcher="Bash|apply_patch",
        post_matcher="apply_patch",
        placements=(
            ("skills/clean-code", HOME / ".agents/skills/clean-code"),
            ("skills/clean-options", HOME / ".agents/skills/clean-options"),
            ("skills/clean-sweep", HOME / ".agents/skills/clean-sweep"),
        ),
        next_step="Open it, run /hooks and trust the two clean_check hooks.",
    ),
}


def hook_entry(matcher: str, mode: str, timeout: int) -> dict:
    command = 'python3 "{}" {}'.format(CHECK, mode)
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": timeout}]}


def merge_hooks(agent: Agent, removing: bool) -> None:
    """Drop whatever this tool installed before, then put it back unless we are removing it."""
    file = agent.settings
    text = file.read_text() if file.is_file() else ""
    data = json.loads(text) if text.strip() else {}
    hooks = data.setdefault("hooks", {})

    def ours(group: dict) -> bool:
        return any(str(CHECK) in h.get("command", "") for h in group.get("hooks", []))

    for event in ("PreToolUse", "PostToolUse"):
        hooks[event] = [g for g in hooks.get(event, []) if not ours(g)]
    if not removing:
        hooks["PreToolUse"].append(hook_entry(agent.pre_matcher, "pre", 10))
        hooks["PostToolUse"].append(hook_entry(agent.post_matcher, "post", 15))
    for event in ("PreToolUse", "PostToolUse"):
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        data.pop("hooks")

    file.parent.mkdir(parents=True, exist_ok=True)
    if text:
        file.with_suffix(file.suffix + ".bak").write_text(text)
    file.write_text(json.dumps(data, indent=2) + "\n")


def place(source: Path, target: Path, removing: bool) -> None:
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    if removing:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)


def install_core(src: Path, removing: bool) -> None:
    if removing:
        shutil.rmtree(CORE, ignore_errors=True)
        return
    place(src / "core/clean_code", CORE / "clean_code", removing=False)
    shutil.copy2(src / "core/clean_check.py", CHECK)
    CHECK.chmod(0o755)


def chosen(argv: list[str]) -> tuple[list[str], bool]:
    known = {"--uninstall"} | {"--" + key for key in AGENTS}
    unknown = [a for a in argv if a not in known]
    if unknown:
        raise SystemExit("clean-code: unknown option " + unknown[0])
    named = [key for key in AGENTS if "--" + key in argv]
    return (named or [key for key, agent in AGENTS.items() if agent.present()]), "--uninstall" in argv


def main(argv: list[str]) -> int:
    src = Path(__file__).resolve().parent.parent.parent
    targets, removing = chosen(argv)
    if not targets:
        print("clean-code: no agent found. Pass --claude or --codex.", file=sys.stderr)
        return 1
    install_core(src, removing)
    for key in targets:
        agent = AGENTS[key]
        merge_hooks(agent, removing)
        for relative, target in agent.placements:
            place(src / relative, target, removing)
        print("{}: {}".format(agent.name, "removed." if removing else "installed. " + agent.next_step))
    return 0
