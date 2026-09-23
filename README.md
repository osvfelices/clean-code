<p align="center"><img src="assets/executor.png" alt="" width="180"></p>

<h1 align="center">clean-code</h1>

<p align="center"><strong>Code should leave no residue.</strong></p>

<p align="center">Edit hooks for Claude Code and Codex. They hold the lines an agent just wrote to account,<br>
leave the code that was already there alone, and say so when they cannot tell.</p>

```bash
npx github:osvfelices/clean-code install
```

---

## Why

An agent writes code faster than anyone reviews it, and it leaves things behind a person rarely
would: a comment narrating the edit it just made, a `console.log` from the attempt that failed, an
`as any` that silenced the type error instead of answering it, a suppression above the line it was
meant to fix. None of it breaks a build, so nothing catches it. Each piece is too small to schedule.
It is paid for on every later read.

The cheap moment to remove it is the moment it is written, by the agent that wrote it, while the
edit is still in front of it. That is where this runs.

## After an edit

One of three things happens, and the agent can tell them apart.

**Nothing to fix.** No output at all. A clean edit costs no context.

**A finding.** A short report, by line, of what this edit introduced:

```
Clean up these in the file you are editing (line numbers), keep all types:
app/cart.ts
  debug output: L2
```

**Cannot tell.** The hook says it did not check, instead of implying it did:

```
clean-code: card.tsx not checked, JSX needs tree-sitter to be read reliably
```

That last one is the point. A checker that falls silent when it is unsure trains everyone to read
silence as approval.

Before an edit lands, a second hook blocks the moves that hide a problem rather than solve it:
removing a type annotation, turning off strict mode or a lint rule, bypassing a commit hook,
deleting a test, editing the checker's own files. Prose is exempt, so documentation can quote a
setting it does not change.

## What it checks

```
residue        debug-output  todo-marker  commented-code
comments       narration-comment  banner-comment  closing-brace-comment  redundant-docstring
               comment-typography  comment-shouting  phase-label  header-essay  comment-density
safety         suppressed-check  weak-type  non-null-assertion  swallowed-error
               skipped-test  hardcoded-secret
structure      too-many-arguments  flag-argument        (with tree-sitter)
boundaries     client-bundles-server-code
```

`clean-code rules` lists where each applies; `clean-code explain <rule>` says what it means. The
comment rules encode a house style; switch any of them off per project.

Code rules read code. A string, a template's text, a regex body and JSX text are data, so
`"console.log('example')"` is not a debug call and `SQL = """..."""` is not a docstring, while
`${console.log(x)}` and `{console.log(x)}` in JSX are code and are found.

The structure rules make narrow claims, because a syntax tree shows the shape of a signature, not
the design behind it. `too-many-arguments` counts what every caller must pass, and leaves alone a
signature someone else chose: an inline callback, a JSX prop, an `override`. `flag-argument` fires
only where a boolean decides a branch, and asks you to check for two behaviors rather than telling
you to split.

### The boundary rule

One rule reads past its file. A `"use client"` module that gains a runtime import path to code
that cannot run in a browser fails the Next.js build after types, tests and lint have all passed.
This rule catches that at the edit.

It follows static, side-effect, re-export, `import()` and `require()` edges to any depth, through
relative paths and the `paths` and `baseUrl` of the nearest `tsconfig.json` or `jsconfig.json`.
`import type` is erased and is not an edge; a `"use server"` module ends a path, because a client
reaches it by RPC. Server-only means a Node builtin Next.js does not replace in a client build
(`fs`, `net`, `child_process`; not `path`, `crypto` or `http`, which it does), `server-only`,
`next/headers`, or a short list of packages with no browser build unless their `package.json`
names one. The finding lands on the import that opens the path. An import it cannot resolve, or a
graph past 2,000 modules, is reported as not checked.

It is deliberately narrow: one boundary, stated precisely, not a general architecture checker.

## Evidence

`core/tests/bench.py` reproduces every figure here from fixed inputs in
`core/tests/bench_cases.json`. Two runs give the same counts and bytes.

| | without tree-sitter | with it |
| --- | --- | --- |
| 43 labeled edits: defects found / missed / invented | 22 / 0 / 0 | 25 / 0 / 0 |
| clean edits reported clean | 16 | 17 |
| not checked, each one expected | 5 | 1 |
| 48 real edits replayed from this repository: silent | 47 (98%) | 46 (96%) |
| bytes the agent reads, total and per edit | 113, 2.4 | 287, 6.0 |
| hook time per edit, median | 50 to 65 ms | 55 to 80 ms |

Read these for what they are. The labeled edits were written to exercise each rule, so 0 missed
and 0 invented says the rules do what they claim on those cases, not that they catch everything.
The replayed edits are mostly Python, so their silence says little about a React codebase, where
the comment rules speak up far more often. Times depend on the machine.

The comment reader was checked against tree-sitter on every JS-family file both accept in VS
Code's `src` at `dd35b1a` (9,416 files) with no mismatch, and Python comments against tree-sitter
on the CPython standard library.

## Languages

| | with tree-sitter | without it |
| --- | --- | --- |
| `.ts` | parsed; scanned where the grammar rejects valid code | scanned |
| `.tsx` `.jsx` | parsed; not checked where rejected | not checked |
| `.js` `.mjs` `.cjs` | parsed; scanned where rejected and JSX is impossible | scanned until a `<` could open JSX, then not checked |
| `.py` | the running Python's own parser; newer syntax than it knows is not checked | same |

React and Next.js are covered through TSX, JSX and the boundary rule. Go, Rust, PHP, Java, Kotlin,
Swift, C#, Scala, Ruby and shell are read by the same lexical scanner on a best-effort basis;
nothing here verifies how it reads them.

## Install

```bash
npx github:osvfelices/clean-code install              # every agent found
npx github:osvfelices/clean-code install --claude     # or --codex
npx github:osvfelices/clean-code install --no-parser  # skip tree-sitter
npx github:osvfelices/clean-code uninstall
```

The installer builds a private Python environment in `~/.clean-code/venv` with pinned tree-sitter
grammars, so a hook never downloads anything, and it ends with one line saying whether React, TSX
and JSX checking is on. With `--no-parser`, or without network access, it is off, and every `.tsx`
and `.jsx` edit is reported as not checked, even if the Python running the hook has a parser of
its own.

Settings are merged, never replaced. Every settings file is validated before anything changes; the
new files are staged beside the old ones and swapped in by rename, and any failure swaps the old
ones back. Earlier clean-code hooks are recognized by the command they run and removed; anything
else, including a hook that merely mentions the checker, stays. Installing twice leaves one hook.

| | Claude Code | Codex |
| --- | --- | --- |
| hooks | `~/.claude/settings.json` | `~/.codex/hooks.json` |
| skill | `~/.claude/skills/clean-code` | `~/.agents/skills/clean-code` |
| sweep | `/clean [path]` | `$clean-sweep` |
| design options | `/options <task>` | `$clean-options` |
| review | agent `clean-reviewer` | not available |

Codex asks you to review and trust new hooks once: run `/hooks` in it after installing.

Requires Python 3.9 or newer, and Node 18 or newer for `npx` only.

## Configuration

`.clean-code.json` at the project root, the directory the agent names or the top of the git
repository. One file per project, no inheritance.

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

An unknown key, an unknown rule, a value of the wrong type or range, or JSON that does not parse
stops the check, and the agent is told why. A misconfigured checker does not quietly fall back to
defaults.

## Design decisions

**Only the edit is accountable.** Editing one line of a legacy module used to report everything
already wrong with it; on one real working tree, 167 of 169 findings were inherited. The lines come
from the edit's own coordinates, Claude Code's `structuredPatch` or each Codex hunk located by its
context, never from matching text. Identical text elsewhere is not the edit, and a deletion
introduces nothing. When the lines cannot be established, the file is not checked; it is never
billed whole.

**Read-only.** The hook reports and never writes to the file it checks. An automatic fixer that
cannot tell a comment from a string corrupts code, and no amount of convenience is worth that.

**Precision is earned on other people's code.** A rule that fires heavily on VS Code or the Python
standard library is wrong, not them. That test removed a function-length rule (9% of real React
components), turned an acronym allowlist into a closed set of emphasis words, and taught the
test-skip rule that a conditional skip is a platform guard.

## Limitations

- TSX and JSX need the installer's tree-sitter environment; without it they are not checked.
- Claude Code is checked on Edit, Write and MultiEdit, Codex on `apply_patch`. A file written by a
  shell command is not seen.
- The boundary rule follows webpack's Node substitutions for client builds; Turbopack's are not
  verified. Package `imports` fields and package-based `extends` in tsconfig are not resolved, so
  those edges are reported as not checked. An import used only as a type but written without
  `type` counts as a runtime edge. Finding the clients above an edited server module can take a
  few seconds on a large app.
- The installer is POSIX only and needs network access for the grammars.

## Development

```bash
python3 core/tests/test_clean_check.py   # the suite, a plain script; unittest discovery finds nothing
python3 core/tests/bench.py --gate       # fails when a labeled edit comes out other than expected
python3 core/clean_check.py files core/clean_code/*.py   # the checker on its own source
```

CI runs all three on Python 3.9 and 3.14, with and without the pinned grammars. `BASELINE.md`
keeps the measurements and the corpora they come from.

```
core/clean_code/
  source.py      what is a comment, what is data, and which reader owns each language
  hooks.py       which lines an edit introduced; what to block before it
  rules.py       the rules and their registry
  ast_rules.py   the rules that need a syntax tree
  boundary.py    the client/server boundary
  config.py      .clean-code.json and where the project starts
  install.py     staged, all-or-nothing install and uninstall
  cli.py         entry point
```
