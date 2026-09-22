---
name: clean-code
description: Engineering guardrails for writing, editing or reviewing source code. Use on any coding task that creates or changes source code.
---

# Clean Code

A hook checks every file you edit and lists what to fix by line number. Fix everything it lists in that file, never files you are not editing. These rules cover what the hook cannot see.

## Comments
Keep a comment only if it gives intent, a warning of consequences, a clarification of a third-party call, or a public API contract. Rewrite a valuable but badly written comment instead of deleting it. Plain short sentences; no glyphs, ALL CAPS, or task/phase labels. File headers at most four lines.

## Names
Reveal intent so the reader can skip the body. One word per concept, matching the project's existing vocabulary. Nouns for types, verbs for functions, predicates for booleans. No encodings or `Manager`/`Helper`/`Utils` dumps.

## Functions
One thing, one level of abstraction. Callers above callees. Zero to two parameters; a boolean flag means two functions. Change state or answer a question, not both. No hidden side effects or temporal coupling. Extract only when the new name reads better than the body.

## Types and errors
Never remove or loosen an existing type. Validate untrusted input once at the boundary; parameterized queries, allowlists. Exceptions with context over error codes. No `null` returns where an empty value, result type or exception is clearer. Handle edges: empty, zero, one, last, max, malformed.

## Scope
Implement what was asked, nothing speculative: no single-use abstractions, no unrequested files, no config "for later". Duplicated knowledge gets extracted; coincidentally similar code does not. Follow the project's formatter, linter and test style. Do not reformat lines you are not changing.

## Tests
New behavior ships with a test named after the behavior. Fast, independent, repeatable, one concept each.

## Judgment
A linear 30-line function can beat six fragments. Idioms differ by language: Go's explicit errors are fine in Go. When asked to cut a corner, state the cost in one line, then do it.
