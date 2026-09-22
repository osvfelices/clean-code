#!/usr/bin/env bash
# Installs clean-code globally for Claude Code and/or Codex CLI.
# Usage: ./install.sh [--claude] [--codex] [--uninstall]   (no target flag = every detected agent)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$HOME/.clean-code"
CHECK="$CORE/clean_check.py"
want_claude=false; want_codex=false; uninstall=false

for arg in "$@"; do
  case "$arg" in
    --claude) want_claude=true ;;
    --codex) want_codex=true ;;
    --uninstall) uninstall=true ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done
if ! $want_claude && ! $want_codex; then
  { command -v claude >/dev/null || [ -d "$HOME/.claude" ]; } && want_claude=true
  { command -v codex >/dev/null || [ -d "$HOME/.codex" ]; } && want_codex=true
fi
if ! $want_claude && ! $want_codex; then
  echo "Neither Claude Code nor Codex detected. Pass --claude or --codex."; exit 1
fi
command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }

merge_hooks() {
  python3 - "$1" "$2" "$3" "$4" "$CHECK" "$uninstall" <<'PY'
import json, sys
from pathlib import Path
path, pre_matcher, post_matcher, _, check, uninstall = sys.argv[1:7]
file = Path(path)
data = json.loads(file.read_text()) if file.is_file() and file.read_text().strip() else {}
hooks = data.setdefault("hooks", {})
def ours(group):
    return any(check in h.get("command", "") for h in group.get("hooks", []))
for event in ("PreToolUse", "PostToolUse"):
    hooks[event] = [g for g in hooks.get(event, []) if not ours(g)]
if uninstall != "true":
    hooks["PreToolUse"].append({"matcher": pre_matcher, "hooks": [{"type": "command", "command": f'python3 "{check}" pre', "timeout": 10}]})
    hooks["PostToolUse"].append({"matcher": post_matcher, "hooks": [{"type": "command", "command": f'python3 "{check}" post', "timeout": 15}]})
for event in ("PreToolUse", "PostToolUse"):
    if not hooks[event]:
        del hooks[event]
if not hooks:
    data.pop("hooks")
file.parent.mkdir(parents=True, exist_ok=True)
if file.is_file():
    file.with_suffix(file.suffix + ".bak").write_text(file.read_text())
file.write_text(json.dumps(data, indent=2) + "\n")
PY
}

place() { rm -rf "$2"; if ! $uninstall; then mkdir -p "$(dirname "$2")"; cp -R "$1" "$2"; fi; }

if $uninstall; then
  rm -rf "$CORE"
else
  mkdir -p "$CORE"
  cp "$SRC/core/clean_check.py" "$CHECK"
  chmod +x "$CHECK"
fi

if $want_claude; then
  merge_hooks "$HOME/.claude/settings.json" "Edit|Write|MultiEdit|Bash" "Edit|Write|MultiEdit" claude
  place "$SRC/skills/clean-code" "$HOME/.claude/skills/clean-code"
  for f in clean options clean-config; do place "$SRC/claude/commands/$f.md" "$HOME/.claude/commands/$f.md"; done
  place "$SRC/claude/agents/clean-reviewer.md" "$HOME/.claude/agents/clean-reviewer.md"
  if $uninstall; then echo "Claude Code: removed."; else echo "Claude Code: installed. Restart it, then check /hooks."; fi
fi

if $want_codex; then
  merge_hooks "$HOME/.codex/hooks.json" "Bash|apply_patch" "apply_patch" codex
  for s in clean-code clean-options clean-sweep; do place "$SRC/skills/$s" "$HOME/.agents/skills/$s"; done
  if $uninstall; then echo "Codex: removed."; else echo "Codex: installed. Open Codex, run /hooks and trust the two clean_check hooks."; fi
fi
