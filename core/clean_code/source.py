"""One file split into code with comments blanked out, plus the comments themselves.

Every rule reads a Source. Nothing here knows what a violation is.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import warnings
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from .config import C_LIKE, HASH_LIKE, JS_LIKE

def grammar_loader():
    """A function from grammar name to parser: the per-language grammars the installer provides, which
    carry their compiled grammar, else tree-sitter-language-pack, else None."""
    try:
        import tree_sitter
        import tree_sitter_javascript
        import tree_sitter_python
        import tree_sitter_typescript
    except ImportError:
        try:
            from tree_sitter_language_pack import get_parser as from_pack
        except ImportError:
            return None
        return from_pack
    languages = {"typescript": tree_sitter_typescript.language_typescript, "tsx": tree_sitter_typescript.language_tsx,
                 "javascript": tree_sitter_javascript.language, "python": tree_sitter_python.language}
    return lambda name: tree_sitter.Parser(tree_sitter.Language(languages[name]()))


# An installation made without a parser leaves this file in its core, and then no parser is loaded,
# whatever the interpreter running the hook happens to hold.
PARSER_OFF = Path(__file__).resolve().parent.parent / "parser-off"
get_parser = None if PARSER_OFF.is_file() else grammar_loader()

GRAMMAR_BY_SUFFIX = {".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "javascript",
                     ".mjs": "javascript", ".cjs": "javascript"}

# JSX files are read by a parser that accepts them or not at all.
PARSER_ONLY = frozenset({".tsx", ".jsx"})

# JavaScript's grammar allows JSX as well, so the scanner reads it only while every < is an operator.
JSX_ALLOWED = frozenset({".js", ".mjs", ".cjs"})

# A < is an operator only after an operand: a name that is not a keyword, a number, ) ] or a literal.
OPERAND_END = re.compile(r"([A-Za-z_$][\w$]*|[\d.]+\w*|[)\]'\"`])\s*$")
NOT_OPERANDS = frozenset("await break case catch class const continue debugger default delete do else enum export "
                         "extends finally for function if import in instanceof let new of return static switch "
                         "throw try typeof var void while with yield".split())

# A value may start here, so a slash opens a regex literal.
VALUE_MAY_START = re.compile(r"(^|[(,=:\[!&|?{};+\-*%~^<>]|\b(return|typeof|case|in|of|do|else|yield|await|default))\s*$")

COMMENT_TEXT = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")

STRING_QUOTES = re.compile(r"""^[rRuUbBfF]*('''|\"\"\"|'|")|('''|\"\"\"|'|")$""")

STRING_OPENING = re.compile(r"""^[A-Za-z]*('''|\"\"\"|'|")""")

# Parser nodes whose whole text is data: a regex body, JSX text and the entities inside it.
DATA_NODES = {"regex_pattern", "jsx_text", "html_character_reference"}


class Unparsable(ValueError):
    """No reader can be trusted with this file, so no rule can speak for it."""


@dataclass
class Comment:
    line: int
    text: str


@dataclass
class Source:
    path: Path
    lines: list[str]
    # Comments blanked out; strings stay, because an import specifier or a directive is read there.
    code_lines: list[str]
    comments: list[Comment]
    tree: object | None = None
    # Which reading found the comments: "python", "tree-sitter", or the "lexical" scanner.
    model: str = "lexical"
    # Comments and the contents of strings, templates, regexes and JSX text blanked out. Quotes stay,
    # so a rule can still see that a literal stands there. Code rules read this.
    executable_lines: list[str] | None = None
    # The lines a hook edit introduced, or None when the whole file is being checked.
    edited: set[int] | None = None


Span = tuple[int, int, str]
Extent = tuple[int, int]


@dataclass
class Reading:
    """What one reader found in a file: its comments, and the extents that hold data rather than code."""
    comments: list[Span]
    literals: list[Extent]
    model: str


def split_comments(text: str, ext: str) -> tuple[list[str], list[Comment]]:
    """Return code with comments blanked out (line-preserving) and the extracted comments."""
    return assemble(text, read(text, ext).comments)


def read(text: str, ext: str) -> Reading:
    """Read a file with the one reader trusted for it, or raise Unparsable.

    Python is read by its own parser. A JS-family file is read by tree-sitter when it is installed and
    accepts the file. Otherwise .tsx and .jsx are not read at all, and the rest by the lexical scanner,
    which stops at the first < that could open JSX.
    """
    if ext == ".py":
        return python_reading(text)
    if get_parser and ext in GRAMMAR_BY_SUFFIX:
        found = parsed_reading(text, GRAMMAR_BY_SUFFIX[ext])
        if found is not None:
            return found
    if ext in PARSER_ONLY:
        raise Unparsable("JSX needs tree-sitter to be read reliably" if get_parser is None
                         else "tree-sitter rejected its syntax, and JSX is read by a parser or not at all")
    return scanned_reading(text, ext)


def assemble(text: str, spans: list[Span]) -> tuple[list[str], list[Comment]]:
    """Blank each (start, end, comment text) span out of the code, keeping every newline where it was."""
    starts = line_starts(text)
    comments = [Comment(bisect_right(starts, start), body) for start, _, body in sorted(spans)]
    return blank(text, [(start, end) for start, end, _ in spans]).split("\n"), comments


def blank(text: str, extents: list[Extent]) -> str:
    """Spaces over every character of each extent except newlines, so lines and columns keep their place."""
    out, at = [], 0
    for start, end in sorted(extents):
        start = max(start, at)
        if end <= start:
            continue
        out.append(text[at:start])
        out.append(re.sub(r"[^\n]", " ", text[start:end]))
        at = end
    out.append(text[at:])
    return "".join(out)


def line_starts(text: str) -> list[int]:
    return [0] + [m.end() for m in re.finditer("\n", text)]


def python_reading(text: str) -> Reading:
    """Comments and strings from the tokenizer, docstrings from the syntax tree."""
    try:
        with warnings.catch_warnings():
            # An invalid escape is the author's warning to read, not something to print into a hook.
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError, ValueError) as exc:
        raise Unparsable(f"not valid Python at line {getattr(exc, 'lineno', '?')}") from exc
    lines = text.split("\n")
    starts = line_starts(text)

    def offset(row: int, column: int) -> int:
        return starts[row - 1] + column

    comments = [(offset(*t.start), offset(*t.end), t.string) for t in tokens
                if t.type == tokenize.COMMENT and not (t.start == (1, 0) and t.string.startswith("#!"))]
    # Since 3.12 an f-string is split into its text and its replacement fields, which are code.
    middles = {getattr(tokenize, name) for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE") if hasattr(tokenize, name)}
    literals = string_contents(tokens, offset, middles)
    if not middles:
        fields = f_string_fields(tree, text, lines, starts)
        literals = without(literals, fields) + [s for start, end in fields for s in strings_within(text, start, end)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            doc = node.body[0]
            if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant) and isinstance(doc.value.value, str):
                start = starts[doc.lineno - 1] + char_column(lines[doc.lineno - 1], doc.col_offset)
                end = starts[doc.end_lineno - 1] + char_column(lines[doc.end_lineno - 1], doc.end_col_offset)
                comments.append((start, end, STRING_QUOTES.sub("", text[start:end])))
    return Reading(comments, literals, "python")


def string_contents(tokens: list, offset, middles: set) -> list[Extent]:
    """The inside of every string token, quotes and prefix excluded, and every f-string text part."""
    contents = []
    for t in tokens:
        if t.type == tokenize.STRING:
            opening = STRING_OPENING.match(t.string)
            contents.append((offset(*t.start) + opening.end(), offset(*t.end) - len(opening.group(1))))
        elif t.type in middles:
            contents.append((offset(*t.start), offset(*t.end)))
    return contents


def f_string_fields(tree: ast.AST, text: str, lines: list[str], starts: list[int]) -> list[Extent]:
    """Where each f-string replacement field sits, for a Python whose tokenizer keeps an f-string whole.

    The syntax tree places each field. It misplaces some, so each one must parse back into the same
    expression; a field that does not, or that nests another f-string, makes the file unreadable.
    """
    fields = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        field = node.value
        start = starts[field.lineno - 1] + char_column(lines[field.lineno - 1], field.col_offset)
        end = starts[field.end_lineno - 1] + char_column(lines[field.end_lineno - 1], field.end_col_offset)
        nested = any(isinstance(inner, ast.JoinedStr) for inner in ast.walk(field))
        if nested or not parses_as(text[start:end], field):
            version = f"{sys.version_info[0]}.{sys.version_info[1]}"
            raise Unparsable(f"an f-string field at line {field.lineno} cannot be located by Python {version}")
        fields.append((start, end))
    return fields


def parses_as(source: str, node: ast.AST) -> bool:
    try:
        return ast.dump(ast.parse(f"({source})", mode="eval").body) == ast.dump(node)
    except SyntaxError:
        return False


def strings_within(text: str, start: int, end: int) -> list[Extent]:
    """The string contents inside one located f-string field, read by the tokenizer."""
    wrapped = f"({text[start:end]})"
    starts = line_starts(wrapped)
    tokens = tokenize.generate_tokens(io.StringIO(wrapped).readline)
    # The wrapping parenthesis shifts every offset by one.
    return string_contents(list(tokens), lambda row, column: start - 1 + starts[row - 1] + column, set())


def without(extents: list[Extent], holes: list[Extent]) -> list[Extent]:
    kept = []
    for start, end in extents:
        for hole_start, hole_end in sorted(holes):
            if hole_end <= start or hole_start >= end:
                continue
            if hole_start > start:
                kept.append((start, hole_start))
            start = max(start, hole_end)
        if start < end:
            kept.append((start, end))
    return kept


def parsed_reading(text: str, grammar: str) -> Reading | None:
    """Comments and data extents from tree-sitter, or None when it cannot give a tree free of errors.

    An error tree is not trusted for any position. The grammar rejects some valid code, among it a
    bare & in JSX text, sql<Row[]>`...` and typeof import("x"), so an error is not proof of broken code.
    """
    try:
        tree = get_parser(grammar).parse(text.encode("utf-8"))
    except Exception:
        return None
    if tree.root_node.has_error:
        return None
    lines = text.split("\n")
    starts = line_starts(text)

    def offset(point) -> int:
        return starts[point[0]] + char_column(lines[point[0]], point[1])

    comments, literals, stack = [], [], [tree.root_node]
    while stack:
        node = stack.pop()
        start, end = offset(node.start_point), offset(node.end_point)
        if node.type in ("comment", "html_comment"):
            comments.append((start, end, text[start:end]))
            continue
        if node.type in DATA_NODES:
            literals.append((start, end))
        elif node.type == "string" and node.is_named:
            # Named, because the keyword `string` in a type annotation is an unnamed node of that type.
            literals.append((start + 1, end - 1))
        elif node.type in ("template_string", "template_literal_type"):
            # The text between substitutions is data; each ${...} is code or a type and is walked below.
            at = start + 1
            for child in node.children:
                if child.type in ("template_substitution", "template_type"):
                    literals.append((at, offset(child.start_point)))
                    at = offset(child.end_point)
            literals.append((at, end - 1))
        elif node.type == "ternary_expression":
            # The TypeScript grammar's scanner for `?` skips a comment in front of it without emitting
            # a node. Only whitespace and comments can sit in that gap, so they are read from the text.
            question = next((c for c in node.children if c.type == "?"), None)
            if question is not None:
                gap = offset(node.children[0].end_point)
                comments += [(gap + m.start(), gap + m.end(), m.group())
                             for m in COMMENT_TEXT.finditer(text[gap:offset(question.start_point)])]
        stack.extend(node.children)
    # A grammar that did emit the node found the same span, so each is counted once.
    return Reading(sorted(set(comments)), literals, "tree-sitter")


def char_column(line: str, byte_column: int) -> int:
    """Parsers count columns in UTF-8 bytes; the text is indexed in characters."""
    return byte_column if line.isascii() else len(line.encode("utf-8")[:byte_column].decode("utf-8", "ignore"))


def scanned_reading(text: str, ext: str) -> Reading:
    comments, literals = [], []
    scan(text, ext, comments, literals)
    return Reading(comments, literals, "lexical")


def scan_comments(text: str, ext: str) -> list[Span]:
    return scanned_reading(text, ext).comments


def scan(text: str, ext: str, comments: list[Span], literals: list[Extent]) -> None:
    """The lexical fallback: strings, templates, regex literals and comments, filled into the two lists.

    It does not model JSX text, where an apostrophe, a backtick or a URL is prose. In JavaScript it
    raises Unparsable at the first < that is not an operator, rather than read on and misplace a comment.
    """
    line_marker = "#" if ext in HASH_LIKE else "//"
    c_like = ext in C_LIKE
    templates = ext in JS_LIKE
    # Brace depth inside each open ${...} substitution, innermost last.
    substitutions: list[int] = []
    i, n = 0, len(text)
    if templates and text.startswith("#!"):
        # A hashbang line opens the file and is neither a comment nor code.
        i = n if text.find("\n") == -1 else text.find("\n")
    while i < n:
        ch, two = text[i], text[i:i + 2]
        if substitutions and ch in "{}":
            if ch == "{":
                substitutions[-1] += 1
            elif substitutions[-1]:
                substitutions[-1] -= 1
            else:
                substitutions.pop()
                i = template_text(text, i + 1, literals, substitutions)
                continue
            i += 1
            continue
        if ch == "`" and templates:
            i = template_text(text, i + 1, literals, substitutions)
            continue
        if ch in "'\"`":
            end = string_end(text, i)
            if end:
                literals.append((i + 1, end - 1))
            i = end if end else i + 1
            continue
        if ext in JSX_ALLOWED and ch == "<":
            if not follows_operand(text, i, comments):
                line = text.count("\n", 0, i) + 1
                raise Unparsable(f"the < at line {line} may open JSX, which needs tree-sitter to be read reliably")
            i += len(re.match(r"<<?=?", text[i:]).group())
            continue
        if c_like and ch == "/" and two not in ("//", "/*") and value_may_start(text, i, comments):
            end = regex_end(text, i)
            if end:
                literals.append((i + 1, end - 1))
                i = end
                continue
        if c_like and two == "/*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            comments.append((i, end, text[i:end]))
            i = end
            continue
        if text.startswith(line_marker, i) and not (line_marker == "#" and i == 0 and text.startswith("#!")):
            end = text.find("\n", i)
            end = n if end == -1 else end
            comments.append((i, end, text[i:end]))
            i = end
            continue
        i += 1


def value_may_start(text: str, i: int, comments: list[Span]) -> bool:
    return bool(VALUE_MAY_START.search(code_before(text, i, comments)))


def follows_operand(text: str, i: int, comments: list[Span]) -> bool:
    operand = OPERAND_END.search(code_before(text, i, comments))
    return bool(operand) and operand.group(1) not in NOT_OPERANDS


def code_before(text: str, i: int, comments: list[Span]) -> str:
    """Up to 24 characters of code before i, looking past the comments already found."""
    end = i
    for start, stop, _ in reversed(comments):
        if text[stop:end].strip():
            break
        end = start
    return text[max(0, end - 24):end]


def template_text(text: str, i: int, literals: list[Extent], substitutions: list[int]) -> int:
    """Record template text from i as data; return past its closing backtick, or past a ${ it opens."""
    j = i
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == "`":
            literals.append((i, j))
            return j + 1
        elif text.startswith("${", j):
            literals.append((i, j))
            substitutions.append(0)
            return j + 2
        else:
            j += 1
    literals.append((i, len(text)))
    return len(text)


def string_end(text: str, i: int) -> int | None:
    """Past the string opened at i. A quote that does not close on its own line was prose."""
    quote, j = text[i], i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == quote:
            return j + 1
        elif text[j] == "\n" and quote != "`":
            return None
        else:
            j += 1
    return None


def regex_end(text: str, i: int) -> int | None:
    j, in_class = i + 1, False
    while j < len(text) and text[j] != "\n":
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == "[":
            in_class = True
        elif text[j] == "]":
            in_class = False
        elif text[j] == "/" and not in_class:
            return j + 1
        j += 1
    return None


def load_source(path: Path) -> Source:
    text = path.read_text(encoding="utf-8", errors="replace")
    found = read(text, path.suffix)
    code_lines, comments = assemble(text, found.comments)
    executable = blank(text, [(start, end) for start, end, _ in found.comments] + found.literals)
    return Source(path, text.split("\n"), code_lines, comments, model=found.model,
                  executable_lines=executable.split("\n"))


def hit(src: Source, line: int) -> str:
    return f"{line}: {src.lines[line - 1].strip()[:120]}"


def line_of(h: str) -> int:
    return int(h.split(":", 1)[0])


def grep_code(src: Source, pattern: str) -> list[str]:
    """Lines whose executable code matches: never text inside a comment, a string or JSX."""
    rx = re.compile(pattern)
    return [hit(src, i + 1) for i, l in enumerate(src.executable_lines) if rx.search(l)]


def grep_comments(src: Source, pattern: str) -> list[str]:
    rx = re.compile(pattern, re.IGNORECASE)
    out = []
    for c in src.comments:
        for k, l in enumerate(c.text.split("\n")):
            if rx.search(l):
                out.append(hit(src, c.line + k)); break
    return out
