# clean-code

Clean Code enforcement for Claude Code and Codex. It runs as an editor hook, reads only the lines
the agent just wrote, and says nothing when there is nothing to say.

```bash
npx github:osvfelices/clean-code install
```

Restart the agent afterwards. Codex asks you to trust new hooks once: open it, run `/hooks`, trust both.

---

## Why it exists

A coding agent leaves a particular residue: a comment that narrates the edit it just made, a
`console.log` from the round that did not work, an `as any` that made the type error go away, a
blanket suppression above the line it was meant to fix. None of it breaks a build. All of it
accumulates.

Conventional linters do not look for this, because humans rarely produce it. So this one does, and
it does so where the cost is lowest: at the moment of the edit, before the change is anyone's
problem to review.

## What it costs you

The number that matters is how much the hook prints, because every byte it prints lands in the
agent's context and stays there for the rest of the session.

| | before this work | now |
| --- | --- | --- |
| output per edit, mean | 228 B | **2.2 B** |
| edits that print nothing | 13% | **96%** |
| defects introduced on the edited line and caught | not measured | **12 of 12** |

Measured across 125 simulated edits over VS Code, the Python standard library and a production
Next.js codebase. Reproduce with `python3 core/tests/bench.py`, which fails above 25 bytes per edit.

## The design decisions worth knowing

**It reads the diff, not the file.** Editing one line of a two thousand line legacy module used to
report every pre-existing finding in it. On a real working tree that was 167 of 169 findings, or
98% inherited noise. The hook now filters to the lines the edit introduced. Whole-file scanning
still happens in `files` mode and in CI, because that is what a sweep is for.

**Precision beats recall, every time.** A rule that misfires teaches you to ignore every rule. Each
one is measured against code written by Microsoft and by the Python core team before it ships, on
the premise that a rule firing heavily there is wrong and they are not. Rules that could not reach
that bar were not added: function length flags 9% of real React components, because JSX is verbose
and a long component is not a defect.

**A generated file is not yours to clean.** A header carrying `@generated`, or the marker Go and
protobuf write, takes the file out of scope entirely.

**Uppercase is not shouting.** `PAYABLE`, `RBAC` and `UNION ALL` are names and keywords. The rule
looks for a closed set of emphasis words instead of an open allowlist of acronyms, because that
allowlist can never be finished: one codebase alone held 391 enum values.

**Ordinals are not task labels.** `// Step 1` is a release wizard; `// Phase 3B3A` is residue from a
conversation. Only the second is a finding.

**A conditional skip is a platform guard.** `@unittest.skipUnless(...)` keeps a test honest on the
platform it cannot run on. Only an unconditional skip is an unanswered question.

## Rules

Eighteen by default, plus two that register when tree-sitter is installed.

| | |
| --- | --- |
| **Residue** | `debug-output` `todo-marker` `commented-code` |
| **Comments** | `narration-comment` `banner-comment` `closing-brace-comment` `redundant-docstring` `comment-typography` `comment-shouting` `phase-label` `header-essay` `comment-density` |
| **Safety** | `suppressed-check` `weak-type` `non-null-assertion` `swallowed-error` `skipped-test` `hardcoded-secret` |
| **Structure** (tree-sitter) | `too-many-arguments` `flag-argument` |

```bash
npx github:osvfelices/clean-code rules            # what exists, and where each one applies
npx github:osvfelices/clean-code explain weak-type
```

Some findings need no judgement, so the hook fixes them instead of reporting them: comment glyphs,
decorative banners, closing-brace markers. That costs zero tokens.

A pre-edit hook blocks the moves that hide a problem rather than solve it: removing a type
annotation, turning off strict mode or a lint rule, bypassing a commit hook, deleting a test file,
and edits to the checker's own files. Prose is exempt, so documentation can quote a setting it does
not change.

## Structure rules

```bash
pip install tree-sitter tree-sitter-language-pack
```

Argument count and parameter shape are the two heuristics from Chapter 17 that a regex cannot read
honestly, because real signatures span lines. With tree-sitter present they are enabled for
TypeScript, TSX, JavaScript and Python. Without it nothing changes and nothing warns. Parsing adds
0.8 ms per file, against a hook whose interpreter startup is a hundred times that.

Thresholds came from the corpora, not from the book. Five arguments fires on 2% of VS Code
functions and on none of a React product, which is the rarity a default needs. The flag-argument
rule leaves a one-parameter setter alone, because it takes the value it sets, and leaves a
destructured options object alone, because that is the refactor the rule asks for.

## Per project

`.clean-code.json` at the repository root:

```json
{
  "ignore": ["**/legacy/**"],
  "allowConsole": ["**/scripts/**", "**/bin/**", "**/cli/**"],
  "allowTodo": false,
  "disableRules": [],
  "maxCommentRatio": 0.25,
  "maxArguments": 5
}
```

A rule id that does not exist is reported, not silently ignored. So is a file that will not parse.

## Layout

```
bin/clean-code.js          npx front door
install.sh                 the installer, usable on its own
core/clean_check.py        launcher at a stable path, so installed hooks never change
core/clean_code/
  config.py                which files count, per-project overrides, project root
  source.py                a file as code with comments blanked out, plus the comments
  rules.py                 the rules and the registry that holds them
  ast_rules.py             the rules that need a syntax tree, registered only if there is one
  report.py                violations to the few lines the agent reads, and autofix
  hooks.py                 the hook protocol: edited lines, what to block, what was said before
  cli.py                   entry point
```

A rule declares its id, label, message, languages and scope in one place, and the report, the
config and the CLI all read it from there.

| | Claude Code | Codex |
| --- | --- | --- |
| Hooks | `~/.claude/settings.json` | `~/.codex/hooks.json` |
| Rules skill | `~/.claude/skills/clean-code` | `~/.agents/skills/clean-code` |
| Options | `/options <task>` | `$clean-options` |
| Sweep | `/clean [path]` | `$clean-sweep` |
| Deep review | agent `clean-reviewer` | not available |

Existing settings are merged, never replaced, and a `.bak` is left beside each file.

## Tests

```bash
python3 core/tests/test_clean_check.py   # rule precision, recall, registry, hook protocol
python3 core/tests/bench.py              # output per edit against whatever corpora are on the machine
```

CI runs the suite on Python 3.9 and 3.14, with and without tree-sitter, and the checker must come
back clean on its own source.

Two guards matter more than the count. `test_recall_on_the_edited_line` pins eleven defects that
must stay reported, so tightening precision can never quietly trade away recall. `bench.py` fails
above 25 bytes of output per edit, so a new rule cannot reintroduce the noise.

`BASELINE.md` records the numbers each change has to hold and the corpora to validate against. This
repository turns off `suppressed-check` and `skipped-test` on itself: the patterns those rules look
for appear here as regex literals, which is data, not a defect.

## Updating

```bash
npx github:osvfelices/clean-code install
```

Or from a clone, `git pull && ./install.sh`. The installer copies, so the clone can live anywhere.
`npx github:osvfelices/clean-code uninstall` removes it from every agent.

## Requirements

Python 3.9 or newer. Node 18 or newer for the `npx` entry point only; the installer and the hook
need neither.
