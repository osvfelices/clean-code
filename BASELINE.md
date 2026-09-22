# Baseline

Numbers to beat, or at least not to lose. Measured 2026-09-22 on macOS, Python 3.14.

## Per-edit hook output

The only number that matters day to day: what the hook prints after one edit. Every byte here
lands in the agent's context and stays there.

| | before | after |
|---|---|---|
| mean output per edit | 228 b | 2.2 b |
| median output per edit | 219 b | 0 b |
| edits producing nothing | 13% | 96% |

125 edits simulated across VS Code, the Python standard library and a production TypeScript repo.
Reproduce with `python3 core/tests/bench.py`, which fails above 25 bytes per edit.

## Recall

Eleven defects injected on the edited line, all reported. A twelfth, a section banner, is removed
by autofix instead of reported, which costs nothing and is the better outcome. Pinned by
`test_recall_on_the_edited_line` so precision work can never quietly trade recall away.

## Whole-file hit counts

Whole-file counts stopped driving hook output once post mode narrowed to edited lines, but they
still show which rules were firing on code written by people who know what they are doing.

| rule | VS Code, 400 files | Python stdlib, 155 files |
|---|---|---|
| banner-comment | 415 to 30 | 83 to 83 |
| narration-comment | 106 to 17 | 317 to 58 |
| comment-shouting | 30 to 14 | 169 to 54 |
| commented-code | 58 to 58 | 188 to 163 |
| debug-output | unchanged | 183 to 149 |
| swallowed-error | unchanged | 222 to 215 |

Files carrying at least one violation went from 97% to 53% on VS Code and from 90% to 89% on the
standard library. The standard library barely moves because what remains is true: it really does
carry 90 TODO markers and 83 decorative banners.

## What the fixes were

Each change is traced to a false positive found in one of the corpora, never to an opinion.

1. Post mode reports only lines the edit introduced. 98% of what it used to print was inherited.
2. A license notice is not a section banner. It was telling VS Code to delete its MIT header.
3. `print(..., file=...)` is deliberate output, not debug residue.
4. `StopIteration`, `StopAsyncIteration` and `GeneratorExit` are control flow, not swallowed errors.
5. An assignment whose right side is only words is prose. It flagged `# intense = like bold`.
6. Code indented inside a prose comment block is a quoted example, not commented-out code.
7. Narration is narration when the verb opens the comment. `cannot be added` is ordinary English.
8. Shouting is a closed set of emphasis words, not an open allowlist of acronyms. An uppercase
   identifier is a name. The old rule could never be finished: one repo alone held 391 enum values.
9. A malformed `.clean-code.json` is reported. It used to discard the whole config in silence.

## Method

Any rule change gets validated against all three corpora before shipping, never against one
project, because the tool installs globally and runs on every language it supports. Fixing
precision with a per-project allowlist is not a fix.

- VS Code's `src`, from a clone of `microsoft/vscode` at a pinned commit
- the CPython standard library of the Python being tested
- any working TypeScript repo, for domain vocabulary the first two lack

## Later rounds

| Change | Measured effect |
| --- | --- |
| Generated files skipped | Go sample corpus 100% of files flagged to 0% |
| Prose is not commented-out code | Ruby doc comments opening with `return` stopped firing |
| Ordinals are not task labels | `phase-label` 990 hits to 141 on a Next.js product, the rest real |
| Conditional skips are platform guards | `skipped-test` 277 of 1852 standard library files to 23 |
| License notices exempt from shouting | a BSD warranty disclaimer stopped reading as raised voice |
| Rules became data | a typo in `disableRules` is reported; TypeScript rules stopped running on Go |
| Split into modules | byte identical output across three corpora, 590, 956 and 406 hits before and after |
| Structure rules on tree-sitter | 0.8 ms per file, 0.1 findings per file on a React product |
| Prose exempt from the weakening guard | a README may quote a setting it does not change |
| Client to server boundary | 0 findings on 696 real client modules, catches the two-hop bug that broke a build |
