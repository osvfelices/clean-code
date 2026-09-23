---
name: clean-code
description: Engineering guardrails for writing, editing or reviewing source code, so an edit leaves no residue, keeps every type and reads like the code around it. Use on any coding task that creates or changes source code.
---

# clean-code

After each edit a hook reads the lines you just wrote in TypeScript, TSX, JavaScript, JSX or Python, and in other languages on a best-effort basis. It lists what to fix by line, says `not checked` when it cannot read an edit, and says nothing otherwise. Fix what it lists, in the file you are editing; leave older code alone unless asked. `not checked` is not clean: read those lines yourself. What follows covers what a hook cannot see.

## Comments
Keep a comment that gives intent, a consequence, a constraint the code cannot show, or a public contract. Delete one that narrates the edit or repeats the code; rewrite a useful one that reads badly. Plain sentences: the hook flags decorative glyphs, all-caps emphasis, task or phase labels, and file headers past four lines.

## Names
Reveal intent so the reader can skip the body. One word per concept, in the project's own vocabulary. Nouns for types, verbs for functions, predicates for booleans. No encodings, no `Manager`/`Helper`/`Utils` dumps.

## Functions
One job at one level of abstraction, callers above callees. Keep required parameters few; group related ones into an object. A boolean that picks between two behaviors usually wants two functions; a boolean that is data is fine. Change state or answer a question, not both. No hidden side effects or temporal coupling. Extract only when the new name reads better than the body.

## Types and errors
Never remove or loosen an existing type. Validate untrusted input once, at the boundary: parameterized queries, allowlists. Errors carry context. Return an empty value, a result type or an exception where `null` would hide the case. Handle edges: empty, zero, one, last, max, malformed.

## Scope
Implement what was asked, nothing speculative: no single-use abstractions, no unrequested files, no config "for later". Extract duplicated knowledge, not code that merely looks alike. Follow the project's formatter, linter and test style. Do not reformat lines you are not changing.

## Tests
New behavior ships with a test named after the behavior. Fast, independent, repeatable, one concept each.

## Judgment
A linear 30-line function can beat six fragments. Idioms differ by language: Go's explicit errors are right in Go. When asked to cut a corner, state the cost in one line, then do it.
