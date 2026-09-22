# clean-code

Clean Code enforcement for Claude Code and Codex CLI, installed once, active in every project.

## Install
    ./install.sh              # every agent it detects
    ./install.sh --claude     # only Claude Code
    ./install.sh --codex      # only Codex
    ./install.sh --uninstall  # remove from all

Re-run after pulling updates. Requires python3 3.9+.
Codex asks you to trust new hooks once: open Codex, `/hooks`, trust both.

## What it does
The hook checks only the file the agent just edited. It autofixes comment glyphs, banners and closing-brace markers without spending tokens, then lists what is left by line number. An unchanged list is resent as one line. A pre-edit hook blocks removed types, `strict: false`, lint rules turned off, `--no-verify` and edits to its own files.

| | Claude Code | Codex |
|---|---|---|
| Hooks | `~/.claude/settings.json` | `~/.codex/hooks.json` |
| Rules skill | `~/.claude/skills/clean-code` | `~/.agents/skills/clean-code` |
| Options | `/options <task>` | `$clean-options` |
| Sweep | `/clean [path]` | `$clean-sweep` |
| Config | `/clean-config` | edit `.clean-code.json` |
| Deep review | agent `clean-reviewer` | n/a |

Existing settings are merged, never overwritten; a `.bak` copy is left next to each file.

## Per project
`.clean-code.json` at the repo root:

    {"ignore":["**/legacy/**"],"allowConsole":["**/cli/**"],"allowTodo":false,"disableRules":[],"maxCommentRatio":0.25}

## CI
    python3 ~/.clean-code/clean_check.py files src/

## Tests
    python3 core/tests/test_clean_check.py
