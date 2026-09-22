---
name: clean-options
description: Propose 2-3 design approaches with trade-offs for a task and wait for the user's choice. Only when explicitly invoked.
---
Reply in this shape, no preamble, then stop and wait:

Objetivo: <one line>
A. <name>: <what, 1 line>. Coste: <1 line>. Archivos: <list>
B. ...
C. only if truly different
Recomiendo: <letter>, <one reason>.

A is the simplest thing that works. Each option a different design, max three. No option may weaken types or suppress checks. After the user chooses, implement only that one.
