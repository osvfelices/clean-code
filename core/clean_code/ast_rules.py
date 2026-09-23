"""Rules that need a real syntax tree, which regex cannot give honestly.

They register only when tree-sitter is installed. A syntax tree shows the shape of a signature, not the
design behind it, so each rule states the narrow thing it measures and leaves the judgement to the reader.
"""
from __future__ import annotations

import re

from .rules import Incomplete, rule
from .source import Source, get_parser, hit

AVAILABLE = get_parser is not None

PARSER_BY_SUFFIX = {".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "javascript",
                    ".mjs": "javascript", ".cjs": "javascript", ".py": "python"}
AST_LANGUAGES = frozenset(PARSER_BY_SUFFIX)

FUNCTION_NODES = {"function_declaration", "function_expression", "arrow_function", "method_definition",
                  "function_definition", "generator_function_declaration", "lambda"}
PARAMETER_NODES = {"formal_parameters", "parameters", "lambda_parameters"}
PUNCTUATION = {"(", ")", ",", ":"}
# The receiver, and Python's markers for positional-only and keyword-only parameters.
NOT_ARGUMENTS = {"self", "cls", "/", "*"}

# A parameter a caller may leave out: a default, a rest or variadic parameter, a TypeScript optional one.
OPTIONAL_PARAMETER = re.compile(r"=(?!=)|^\.\.\.|^\*|\?\s*:")
BOOLEAN_PARAMETER = re.compile(r"^([A-Za-z_$][\w$]*)\??\s*(:\s*(boolean|bool)\b|=\s*(true|false|True|False)\s*$)")
SETTER_NAME = re.compile(r"^(set|toggle|mark|enable|disable)[A-Z_]")

# Where a function is written for somebody else's call: passed inline, as a JSX prop, as a keyword.
DICTATED_BY = {"arguments", "argument_list", "jsx_expression", "keyword_argument"}

CONDITIONS = {"if_statement", "ternary_expression", "conditional_expression", "while_statement"}

# A boolean on the left of && or || decides whether a call on the right runs, which is a branch too.
SHORT_CIRCUITS = {"binary_expression": {"&&", "||"}, "boolean_operator": {"and", "or"}}


def syntax_tree(src: Source):
    """Parse once per file. A tree with an error on an edited line cannot be judged there."""
    if src.tree is None:
        try:
            src.tree = get_parser(PARSER_BY_SUFFIX[src.path.suffix]).parse(src.path.read_bytes())
        except Exception as exc:
            raise Incomplete(f"tree-sitter could not parse the file ({exc})") from exc
    root = src.tree.root_node
    if root.has_error and any(src.edited is None or line in src.edited for line in error_lines(root)):
        raise Incomplete("tree-sitter could not parse the edited lines")
    return src.tree


def error_lines(node) -> set[int]:
    lines, stack = set(), [node]
    while stack:
        current = stack.pop()
        if current.type == "ERROR" or current.is_missing:
            lines.update(range(current.start_point[0] + 1, current.end_point[0] + 2))
        elif current.has_error:
            stack.extend(current.children)
    return lines


def functions(src: Source):
    """Every function whose signature its own author chose: name, parameters, first line, node."""
    stack = [syntax_tree(src).root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in FUNCTION_NODES or not node.is_named or node.has_error or dictated(node):
            continue
        holder = next((c for c in node.children if c.type in PARAMETER_NODES), None)
        if holder is None:
            continue
        params = [text(c).strip() for c in holder.children if c.type not in PUNCTUATION]
        params = [p for p in params if p and p not in NOT_ARGUMENTS and not p.startswith("this:")]
        yield name_of(node), params, node.start_point[0] + 1, node


def dictated(node) -> bool:
    """A signature someone else chose: a callback written inline, a JSX handler, an override."""
    parent = node.parent
    return (parent is not None and parent.type in DICTATED_BY) or any(c.type == "override_modifier" for c in node.children)


def name_of(node) -> str:
    named = next((c for c in node.children if c.type in ("identifier", "property_identifier")), None)
    if named is None and node.parent is not None and node.parent.type == "variable_declarator":
        named = node.parent.child_by_field_name("name")
    return text(named) if named is not None else ""


def text(node) -> str:
    return node.text.decode("utf8", "replace")


def tested(node, name: str) -> bool:
    """Whether a condition in the function's body reads the parameter."""
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in CONDITIONS:
            condition = current.child_by_field_name("condition")
            if condition is None and current.type == "conditional_expression" and len(current.children) > 2:
                condition = current.children[2]
            if condition is not None and mentions(condition, name):
                return True
        operator = current.child_by_field_name("operator") if current.type in SHORT_CIRCUITS else None
        if operator is not None and text(operator) in SHORT_CIRCUITS[current.type]:
            left, right = current.child_by_field_name("left"), current.child_by_field_name("right")
            if left is not None and right is not None and mentions(left, name) and contains(right, is_call):
                return True
        stack.extend(current.children)
    return False


def mentions(node, name: str) -> bool:
    return contains(node, lambda n: n.type == "identifier" and text(n) == name)


def is_call(node) -> bool:
    return node.type in ("call_expression", "call")


def contains(node, wanted) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        if wanted(current):
            return True
        stack.extend(current.children)
    return False


def _too_many_arguments(src, cfg, root):
    # What every caller must pass. A parameter a caller may leave out is no burden at the call site.
    return [hit(src, line) for _, params, line, _ in functions(src)
            if sum(1 for p in params if not OPTIONAL_PARAMETER.search(p)) >= cfg.max_arguments]


def _flag_argument(src, cfg, root):
    hits = []
    for name, params, line, node in functions(src):
        # A one-parameter setter takes the value it sets, which is one thing, not a switch.
        if len(params) == 1 and SETTER_NAME.match(name):
            continue
        flags = [m.group(1) for m in map(BOOLEAN_PARAMETER.match, params) if m]
        # Only a boolean that a condition reads selects behavior; one passed on or stored is data.
        if any(tested(node, flag) for flag in flags):
            hits.append(hit(src, line))
    return hits


# Known even when tree-sitter is absent, so a configuration naming them stays valid.
RULE_IDS = frozenset({"too-many-arguments", "flag-argument"})

if AVAILABLE:
    rule("too-many-arguments", "too many required arguments: group the related ones",
         "Every caller must pass maxArguments (5 by default) or more arguments. Group the related ones into an "
         "object. Callbacks and overrides are not counted, since someone else chose their signature.",
         languages=AST_LANGUAGES)(_too_many_arguments)
    rule("flag-argument", "boolean parameter picks a branch: check for two behaviors",
         "A boolean parameter decides a branch inside the function. If the branches are two behaviors, two "
         "functions say so better; if the value is data, name it for what it holds.",
         languages=AST_LANGUAGES)(_flag_argument)
