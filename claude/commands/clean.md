---
description: Run the Clean Code checker on the working tree or a path and fix what it reports
allowed-tools: Bash(python3:*), Bash(git:*), Read, Edit, MultiEdit
---
Run `python3 "$HOME/.clean-code/clean_check.py" files $ARGUMENTS`. Fix each reported line, keep every type, re-run until it prints `clean`. Report changes in one short list.
