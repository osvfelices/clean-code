"""Rules that need a real syntax tree, which regex cannot give honestly.

They register only when tree-sitter is installed, so the checker behaves exactly
as before when it is not. Install with: pip install tree-sitter-language-pack
"""
from __future__ import annotations

import re

from .rules import rule
from .source import Source, hit

try:
    from tree_sitter_language_pack import get_parser
except ImportError:
    get_parser = None

AVAILABLE = get_parser is not None

PARSER_BY_SUFFIX = {".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "javascript",
                    ".mjs": "javascript", ".cjs": "javascript", ".py": "python"}
AST_LANGUAGES = frozenset(PARSER_BY_SUFFIX)

FUNCTION_NODES = {"function_declaration", "function_expression", "arrow_function",
                  "method_definition", "function_definition", "generator_function_declaration"}
PARAMETER_NODES = {"formal_parameters", "parameters"}
PUNCTUATION = {"(", ")", ",", ":"}
IMPLICIT_PARAMS = {"self", "cls"}

FLAG_PARAMETER = re.compile(r"^[A-Za-z_$][\w$]*\??\s*(:\s*boolean\b|=\s*(true|false|True|False)\s*$)")
SETTER_NAME = re.compile(r"^(set|toggle|mark|enable|disable)[A-Z_]")
OPTIONAL_PARAMETER = re.compile(r"=(?!=)|^\.\.\.|^\*|\?\s*:")


def syntax_tree(src: Source):
    """Parse once per file. A Source is built per check, so the tree is reused by every AST rule."""
    if src.tree is None:
        language = PARSER_BY_SUFFIX.get(src.path.suffix)
        if not language:
            return None
        try:
            src.tree = get_parser(language).parse(src.path.read_bytes())
        except Exception:
            return None
    return src.tree


def functions(src: Source):
    """Every function-like node with its name, its parameters and the line it opens on."""
    tree = syntax_tree(src)
    if tree is None:
        return
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in FUNCTION_NODES:
            continue
        holder = next((c for c in node.children if c.type in PARAMETER_NODES), None)
        if holder is None:
            continue
        params = [c.text.decode("utf8", "replace").strip() for c in holder.children if c.type not in PUNCTUATION]
        params = [p for p in params if p and p not in IMPLICIT_PARAMS]
        name = next((c.text.decode("utf8", "replace") for c in node.children
                     if c.type in ("identifier", "property_identifier")), "")
        yield name, params, node.start_point[0] + 1


def _too_many_arguments(src, cfg, root):
    # Count what a caller must pass. A defaulted or rest parameter is not a burden at the call site.
    return [hit(src, line) for _, params, line in functions(src)
            if sum(1 for p in params if not OPTIONAL_PARAMETER.search(p)) >= cfg.max_arguments]


def _flag_argument(src, cfg, root):
    hits = []
    for name, params, line in functions(src):
        # A one-parameter setter takes the value it sets, which is one thing, not a switch.
        if len(params) == 1 and SETTER_NAME.match(name):
            continue
        # A destructured options object is the refactor this rule asks for, not the defect.
        if any(FLAG_PARAMETER.match(p) for p in params if not p.startswith("{")):
            hits.append(hit(src, line))
    return hits


if AVAILABLE:
    rule("too-many-arguments", "too many arguments: gather them into one object",
         "More arguments than the signature can carry. Group the related ones into an object.",
         languages=AST_LANGUAGES)(_too_many_arguments)
    rule("flag-argument", "boolean argument: split the function in two",
         "A boolean argument makes the function do two things. Write the two functions instead.",
         languages=AST_LANGUAGES)(_flag_argument)
