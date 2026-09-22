"""Clean Code enforcement for Claude Code and Codex CLI.

The public surface the hooks, the tests and any CI script use.
"""
from .config import CHECKED, C_LIKE, Config, HASH_LIKE, TS_LIKE, changed_files, matches_any, project_root
from .hooks import edit_targets, edited_lines, patch_sections, pre_check
from .report import autofix, format_report
from .rules import BY_ID, RULES, Rule, Violation, check_file, is_generated, shouts
from .source import Comment, Source, hit, line_of, load_source, split_comments

__all__ = [
    "CHECKED", "C_LIKE", "Config", "HASH_LIKE", "TS_LIKE", "changed_files", "matches_any", "project_root",
    "edit_targets", "edited_lines", "patch_sections", "pre_check",
    "autofix", "format_report",
    "BY_ID", "RULES", "Rule", "Violation", "check_file", "is_generated", "shouts",
    "Comment", "Source", "hit", "line_of", "load_source", "split_comments",
]
