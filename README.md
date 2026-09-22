# clean-code

A Clean Code hook for Claude Code and Codex. It reads the lines the agent just wrote, not the file
they live in, and it prints 2.2 bytes on the average edit.

```bash
npx github:osvfelices/clean-code install
```

---

## The problem

An agent leaves residue a human rarely would. A comment narrating the edit it just made. A
`console.log` from the round that did not work. An `as any` that silenced the type error instead of
answering it. A suppression above the line it was meant to fix.

None of it breaks a build, so no linter looks for it and no review catches it. It is individually
trivial, so nobody is ever assigned to remove it. And the cost is not in the writing: it is paid
again on every read, by you and by the next agent, forever.

The only moment removing it is cheap is the moment it is written. That is where this runs.

## What it costs

Every byte a hook prints lands in the agent's context and stays there for the session. So that is
the number the project is optimised against, and the number CI enforces.

| | before | now |
| --- | --- | --- |
| output per edit, mean | 228 B | **2.2 B** |
| edits that print nothing | 13% | **96%** |
| defects introduced on the edited line, caught | — | **12 of 12** |

125 simulated edits over VS Code, the CPython standard library and a production Next.js codebase.
`core/tests/bench.py` reproduces it and fails above 25 bytes.

## Six decisions, and the evidence for each

**It reads the diff, not the file.** Editing one line of a legacy module used to report everything
already wrong with it. On a real working tree that was 167 of 169 findings inherited. A tool that
bills you for someone else's debt gets switched off, so this one only answers for the lines the
edit introduced. Whole files are still scanned in `files` mode and in CI, which is what a sweep is.

**Precision beats recall.** One rule that misfires discredits the other nineteen. Every rule is
measured against code written by Microsoft and by the CPython core team before it ships: if a rule
fires heavily there, the rule is wrong and they are not. That test killed candidates. Function
length flags 9% of real React components, because JSX is verbose and a long component is not a
defect, so the rule was not added.

**Uppercase is not shouting.** `PAYABLE`, `RBAC` and `UNION ALL` are enum values, acronyms and SQL.
The original rule held an allowlist of 40 acronyms; one codebase alone answered with 391 enum
values. An allowlist of names can never be finished, so the rule was inverted to a closed set of
emphasis words. False positives fell 82%.

**Ordinals are not task labels.** `// Step 1` is a release wizard. `// Phase 3B3A` is residue from
a conversation. The rule wanted the second and was catching both: 990 findings on a Next.js
product, of which 849 were the product's own vocabulary.

**A conditional skip is a platform guard.** `@unittest.skipUnless(...)` keeps a test honest where
it cannot run. The decorator pattern had no terminator, so `skipIf` matched on the `skip` prefix
and 277 of the 1852 files in the standard library were flagged for guarding a Windows-only path.

**A generated file is not yours to clean.** The Go and protobuf marker, or `@generated`, takes the
file out of scope. It had been telling protoc output to rewrite its own `DO NOT EDIT` line.

## Rules

Eighteen by default. Two more register when tree-sitter is importable.

| | |
| --- | --- |
| **Residue** | `debug-output` `todo-marker` `commented-code` |
| **Comments** | `narration-comment` `banner-comment` `closing-brace-comment` `redundant-docstring` `comment-typography` `comment-shouting` `phase-label` `header-essay` `comment-density` |
| **Safety** | `suppressed-check` `weak-type` `non-null-assertion` `swallowed-error` `skipped-test` `hardcoded-secret` |
| **Structure** | `too-many-arguments` `flag-argument` |

```
$ clean-code explain flag-argument
flag-argument
A boolean argument makes the function do two things. Write the two functions instead.
applies to: .cjs .js .jsx .mjs .py .ts .tsx
scope: the edited lines
```

Findings that need no judgement are fixed rather than reported: comment glyphs, decorative banners,
closing-brace markers. That costs nothing and says nothing.

Before an edit lands, a second hook blocks the moves that hide a problem instead of solving it:
removing a type annotation, turning off strict mode or a lint rule, bypassing a commit hook,
deleting a test, and edits to the checker's own files. Prose is exempt, so documentation can quote
a setting it does not change.

## Structure rules

```bash
pip install tree-sitter tree-sitter-language-pack
```

Argument count and parameter shape are the two heuristics a regex cannot read honestly, because
real signatures span lines. With tree-sitter they cover TypeScript, TSX, JavaScript and Python.
Without it nothing changes and nothing warns. Parsing costs 0.8 ms per file against an interpreter
startup a hundred times that.

Thresholds came from the corpora. Five arguments fires on 2% of VS Code functions and none of a
React product. It counts what a caller must pass, so a defaulted or rest parameter does not count:
that distinction alone removed 41 of 71 findings in the standard library, where keyword arguments
are the idiom. `flag-argument` leaves a one-parameter setter alone, because it takes the value it
sets, and an options object alone, because that is the refactor the rule is asking for.

## Configuration

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

A rule id that does not exist is reported. So is a file that will not parse. Silence is reserved
for when there is nothing wrong.

## Design

```
bin/clean-code.js          npx front door, finds a usable python and gets out of the way
install.sh                 five lines, so a clone can still run ./install.sh
core/clean_check.py        launcher at a stable path, so installed hook commands never change
core/clean_code/
  config.py                which files count, per-project overrides, where the project starts
  source.py                a file as code with comments blanked out, plus the comments
  rules.py                 the rules and the registry that holds them
  ast_rules.py             the rules that need a syntax tree, registered only if there is one
  report.py                violations to the lines the agent reads, and the fixes needing no judgement
  hooks.py                 the hook protocol: edited lines, what to block, what was already said
  install.py               the agents as a table: settings, matchers, what goes where
  cli.py                   entry point
```

A rule declares its id, label, message, languages and scope once. The report, the config validator
and the CLI all read it from there. Before that, the report kept a parallel table keyed by rule id,
and it had already drifted by two entries.

`source.py` is the load-bearing module: eleven of the twenty rules read its output. It knows that a
slash can open a regex literal, that a character class makes a slash literal, and that only a
backtick or a triple quote may span lines, which is how an apostrophe reaches JSX prose without
swallowing the rest of the file. Each of those was a silent bug that made comment rules fire on
code. It agrees with tree-sitter on 99% of comments across 300 real files.

| | Claude Code | Codex |
| --- | --- | --- |
| Hooks | `~/.claude/settings.json` | `~/.codex/hooks.json` |
| Rules skill | `~/.claude/skills/clean-code` | `~/.agents/skills/clean-code` |
| Options | `/options <task>` | `$clean-options` |
| Sweep | `/clean [path]` | `$clean-sweep` |
| Deep review | agent `clean-reviewer` | not available |

Settings are merged, never replaced. A `.bak` is left beside each file, installing twice leaves one
hook, and uninstalling leaves everyone else's alone. All four are asserted, not assumed.

## Tests

```bash
python3 core/tests/test_clean_check.py   # 39 tests
python3 core/tests/bench.py              # output per edit against whatever corpora are present
```

CI runs the suite on Python 3.9 and 3.14, with and without tree-sitter, and the checker must come
back clean on its own source.

Three guards matter more than the count:

- `test_recall_on_the_edited_line` pins eleven defects that must stay reported, so tightening
  precision can never quietly trade away recall.
- `bench.py` fails above 25 bytes per edit, so a new rule cannot reintroduce the noise.
- `test_installer_merges_and_removes_only_its_own_hooks` starts from settings that already hold a
  stranger's hook, because that is the failure that costs somebody their configuration.

`BASELINE.md` records the numbers each change has to hold and the corpora to validate against.

This repository disables `suppressed-check` and `skipped-test` on itself. The patterns those rules
look for appear here as regex literals, which is data, not a defect.

## Commands

```
clean-code install [--claude] [--codex]   install for the agents found, or the ones named
clean-code uninstall                      remove from every agent
clean-code rules                          what exists and where each rule applies
clean-code explain <rule>                 what one rule means
clean-code files <path...>                check paths, exit non-zero on a finding
```

Update with the install command again, or `git pull && ./install.sh` from a clone. The installer
copies, so the clone can live anywhere.

## Requirements

Python 3.9 or newer. Node 18 or newer for `npx` only; neither the installer nor the hook needs it.
