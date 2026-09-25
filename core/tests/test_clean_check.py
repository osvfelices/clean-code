import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE))
import clean_code as cc
from clean_code import rules as rules_module
from clean_code import source as source_module

ENTRY = CORE / "clean_check.py"

ROOT = Path(tempfile.mkdtemp())
CFG = cc.Config.load(ROOT)


def rules_for(name: str, text: str) -> set[str]:
    path = ROOT / name
    path.write_text(text)
    return {v.rule for v in cc.check_file(path, CFG, ROOT)}


def claude_payload(path: Path, hunks: list, session: str = "") -> dict:
    """What Claude Code sends after an edit: the hunks it applied, as structuredPatch rows."""
    return {"session_id": session, "tool_name": "Edit", "tool_input": {"file_path": str(path)},
            "tool_response": {"filePath": str(path), "structuredPatch": [{"newStart": s, "lines": rows} for s, rows in hunks]}}


def post(payload: dict, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ENTRY), "post"], input=json.dumps(payload), capture_output=True,
                          text=True, env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"}, cwd=str(root))


def test_clean_file_passes():
    assert rules_for("a.ts", "export function add(a: number, b: number): number {\n  return a + b;\n}\n") == set()


def test_string_containing_comment_marker_is_not_a_comment():
    assert "narration-comment" not in rules_for("b.ts", 'const url: string = "http://x/added";\n')
    assert "commented-code" not in rules_for("c.ts", 'const s: string = "// const y = 1;";\n')


def test_debug_output_flagged_and_allowed_in_cli():
    assert "debug-output" in rules_for("d.ts", "console.log(1);\n")
    (ROOT / "cli").mkdir(exist_ok=True)
    assert "debug-output" not in rules_for("cli/run.ts", "console.log(1);\n")


def test_comment_rules():
    assert "narration-comment" in rules_for("e.ts", "// Added validation here\nconst a: number = 1;\n")
    assert "banner-comment" in rules_for("f.ts", "// ---------- helpers ----------\nconst a: number = 1;\n")
    assert "commented-code" in rules_for("g.ts", "// const y: number = 1;\nconst a: number = 1;\n")
    assert "todo-marker" in rules_for("h.py", "# TODO: later\nx = 1\n")
    assert "redundant-docstring" in rules_for("i.ts", "/** Gets the user. */\nfunction getUser(): User { return u; }\n")


def test_safety_rules():
    assert "suppressed-check" in rules_for("j.ts", "// @ts-ignore\nconst a: number = 'x';\n")
    assert "weak-type" in rules_for("k.ts", "const a = b as any;\n")
    assert "non-null-assertion" in rules_for("l.ts", "const n: number = maybe!.value;\n")
    assert "non-null-assertion" not in rules_for("m.ts", "if (a !== b) { run(); }\n")
    assert "skipped-test" in rules_for("n.test.ts", "it.skip('x', () => {});\n")
    assert "swallowed-error" in rules_for("o.ts", "try { a(); } catch (e) {}\n")
    assert "swallowed-error" in rules_for("p.py", "try:\n    a()\nexcept Exception:\n    pass\n")
    assert "hardcoded-secret" in rules_for("q.ts", 'const apiKey: string = "sk_live_abcdefghijklmnop";\n')
    assert "hardcoded-secret" not in rules_for("r.ts", 'const apiKey: string = process.env.API_KEY ?? "";\n')


def test_good_comment_survives():
    text = "/**\n * Retries are capped at three because the upstream API rate-limits bursts.\n */\nexport const MAX_RETRIES: number = 3;\n"
    assert rules_for("good.ts", text) == set()


def test_comment_form_rules():
    assert "comment-typography" in rules_for("u.ts", "// resolve the actor \u00b7 delegate to commands\nconst a: number = 1;\n")
    assert "comment-typography" in rules_for("v.ts", "// map errors \u2014 onto the envelope\nconst a: number = 1;\n")
    assert "comment-shouting" in rules_for("w.ts", "// NO BUSINESS LOGIC here\nconst a: number = 1;\n")
    assert "comment-shouting" not in rules_for("w2.ts", "// derives the UPC and ISRC server-side\nconst a: number = 1;\n")
    assert "phase-label" in rules_for("x.ts", "// Thin Server Actions for Phase 3B3A distributor cutover.\nconst a: number = 1;\n")
    assert "phase-label" in rules_for("x2.ts", "// F50 imported-asset materialization\nconst a: number = 1;\n")
    assert "phase-label" not in rules_for("x3.ts", "// Step 1 fields stay required until the album is complete\nconst a: number = 1;\n")
    assert "phase-label" not in rules_for("x4.ts", "// Phase 0 of the scroll timeline runs from 0.1 to 0.38\nconst a: number = 1;\n")
    header = "/**\n" + "".join(f" * line {i} of a long essay about the module\n" for i in range(7)) + " */\nconst a: number = 1;\n"
    assert "header-essay" in rules_for("y.ts", header)
    short = "/**\n * Owns catalog ingestion commands.\n * Byte upload lives in the API route, not here.\n */\nconst a: number = 1;\n"
    assert "header-essay" not in rules_for("z.ts", short)


def test_python_docstring_density():
    body = "\n".join(f"x{i} = {i}" for i in range(50))
    doc = '"""\n' + "\n".join("line" for _ in range(30)) + '\n"""\n'
    assert "comment-density" in rules_for("s.py", doc + body)


def test_pre_blocks_weakening():
    assert cc.pre_check({"tool_name": "Bash", "tool_input": {"command": "git commit -m x --no-verify"}}, ROOT)
    assert cc.pre_check({"tool_name": "Edit", "tool_input": {"file_path": "tsconfig.json", "old_string": "", "new_string": '"strict": false'}}, ROOT)
    assert cc.pre_check({"tool_name": "Edit", "tool_input": {"file_path": "a.ts", "old_string": "function f(a: number, b: string): void {", "new_string": "function f(a, b) {"}}, ROOT)
    assert cc.pre_check({"tool_name": "Edit", "tool_input": {"file_path": "a.ts", "old_string": "function f(a: number): void {", "new_string": "function f(a: number, b: string): void {"}}, ROOT) is None
    assert cc.pre_check({"tool_name": "Edit", "tool_input": {"file_path": ".clean-code.json", "old_string": "", "new_string": "{}"}}, ROOT)


def test_post_mode_exit_code():
    path = ROOT / "t.ts"; path.write_text("console.log(1);\n")
    payload = claude_payload(path, [(1, ["+console.log(1);"])])
    proc = post(payload, ROOT)
    assert proc.returncode == 2 and "debug output: L1" in proc.stderr
    again = post(payload, ROOT)
    assert again.returncode == 2 and "still open" in again.stderr and len(again.stderr) < 80


PATCH = """*** Begin Patch
*** Update File: svc.ts
@@
-export function load(id: string, strict: boolean): Promise<Row> {
+export function load(id, strict) {
*** Add File: fresh.ts
+console.log("hi");
+const a = b as any;
*** End Patch"""


def test_codex_patch_sections():
    sections = cc.patch_sections(PATCH, ROOT)
    assert [p.name for p, _, _ in sections] == ["svc.ts", "fresh.ts"]
    assert "strict: boolean" in sections[0][1] and "load(id, strict)" in sections[0][2]


def test_codex_pre_blocks_type_removal():
    payload = {"tool_name": "apply_patch", "cwd": str(ROOT), "tool_input": {"command": PATCH}}
    assert "type annotations" in cc.pre_check(payload, ROOT)


def test_codex_post_reports_patched_files():
    (ROOT / "fresh.ts").write_text('console.log("hi");\nconst a = b as any;\n')
    payload = {"tool_name": "apply_patch", "cwd": str(ROOT), "session_id": "codex1", "tool_input": {"command": PATCH}}
    proc = subprocess.run([sys.executable, str(ENTRY), "post"], input=json.dumps(payload),
                          capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, cwd=str(ROOT))
    assert proc.returncode == 2 and "fresh.ts" in proc.stderr and "debug output: L1" in proc.stderr


def test_license_header_is_not_a_banner():
    mit = "/*---------------------------------------------------\n * Copyright (c) Acme. All rights reserved.\n * Licensed under the MIT License.\n *--------------------------------------------------*/\nconst a: number = 1;\n"
    assert "banner-comment" not in rules_for("lic.ts", mit)
    assert "banner-comment" in rules_for("sec.ts", "// ---------- helpers ----------\nconst a: number = 1;\n")


def test_stream_argument_is_deliberate_output():
    assert "debug-output" not in rules_for("warn.py", "import sys\nprint('unavailable', file=sys.stderr)\n")
    assert "debug-output" in rules_for("dbg.py", "print('here')\n")


def test_protocol_exception_is_control_flow():
    assert "swallowed-error" not in rules_for("gen.py", "try:\n    next(it)\nexcept StopIteration:\n    pass\n")
    assert "swallowed-error" not in rules_for("gen2.py", "try:\n    a()\nexcept (GeneratorExit, StopAsyncIteration):\n    pass\n")
    assert "swallowed-error" in rules_for("swallow.py", "try:\n    a()\nexcept ValueError:\n    pass\n")


def test_prose_with_equals_is_not_code():
    assert "commented-code" not in rules_for("prose.py", "# intense = like bold but without being bold\nx = 1\n")
    assert "commented-code" in rules_for("dead.py", "# timeout = compute(x)\nx = 1\n")
    assert "commented-code" in rules_for("dead2.py", "# retries = 3\nx = 1\n")


def test_shouting_is_emphasis_not_identifiers():
    assert "comment-shouting" not in rules_for("enum.ts", "// PAYABLE on a FINALIZED report\nconst a: number = 1;\n")
    assert "comment-shouting" not in rules_for("sql.ts", "// one UNION ALL over the sources\nconst a: number = 1;\n")
    assert "comment-shouting" not in rules_for("sql2.ts", "// columns are optional and SET NULL on delete\nconst a: number = 1;\n")
    assert "comment-shouting" not in rules_for("hyph.ts", "// READ-ONLY view of the ledger\nconst a: number = 1;\n")
    bsd = "/*\n * Copyright (c) 2026 Acme.\n * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS\n * AS IS AND ANY EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED.\n */\nconst a: number = 1;\n"
    assert "comment-shouting" not in rules_for("bsd.ts", bsd)
    assert "comment-shouting" in rules_for("emph.ts", "// NEVER call this from a worker\nconst a: number = 1;\n")
    assert "comment-shouting" in rules_for("emph2.ts", "// this MUST stay in sync with the schema\nconst a: number = 1;\n")


def test_post_mode_scopes_to_edited_lines():
    path = ROOT / "legacy.ts"
    path.write_text("console.log('old');\nconst b = c as any;\n")
    payload = claude_payload(path, [(1, [" console.log('old');", "-const keep: number = 1;", "+const b = c as any;"])], "scope1")
    proc = post(payload, ROOT)
    assert "loose type: write the real type: L2" in proc.stderr, proc.stderr
    assert "debug output" not in proc.stderr, proc.stderr


def test_an_edit_that_cannot_be_located_is_left_unchecked_not_billed_whole():
    path = ROOT / "whole.ts"
    path.write_text("console.log('x');\n")
    kind, said = outcome(post({"session_id": "scope2", "tool_name": "Edit", "tool_input": {"file_path": str(path)}}, ROOT))
    assert kind == "context" and "whole.ts not checked" in said and "debug output" not in said, said


INVALID_CONFIGS = [
    ('{"ignore": ["a",]}', "not valid JSON"),
    ("[]", "must be a JSON object"),
    ('{"disableRule": ["debug-output"]}', 'unknown key "disableRule"'),
    ('{"disableRules": ["coment-shouting"]}', 'unknown rule "coment-shouting"'),
    ('{"disableRules": "debug-output"}', '"disableRules" must be a list of rule ids'),
    ('{"ignore": null}', '"ignore" must be a list of glob patterns'),
    ('{"ignore": ["", "a"]}', '"ignore" must be a list of glob patterns'),
    ('{"allowConsole": [1]}', '"allowConsole" must be a list of glob patterns'),
    ('{"allowTodo": "false"}', '"allowTodo" must be true or false'),
    ('{"maxCommentRatio": "0.3"}', '"maxCommentRatio" must be a number above 0 and at most 1'),
    ('{"maxCommentRatio": 0}', '"maxCommentRatio" must be a number above 0 and at most 1'),
    ('{"maxArguments": 2.5}', '"maxArguments" must be a whole number from 1 to 20'),
    ('{"maxArguments": true}', '"maxArguments" must be a whole number from 1 to 20'),
    ('{"maxArguments": 0}', '"maxArguments" must be a whole number from 1 to 20'),
]


def test_an_invalid_configuration_is_named_and_nothing_is_checked():
    for text, reason in INVALID_CONFIGS:
        box = Path(tempfile.mkdtemp())
        (box / ".clean-code.json").write_text(text)
        (box / "x.ts").write_text("console.log(1);\n")
        sweep = subprocess.run([sys.executable, str(ENTRY), "files", "x.ts"], capture_output=True, text=True,
                               cwd=str(box), env={"PATH": "/usr/bin:/bin"})
        assert sweep.returncode == 1 and reason in sweep.stdout and "not checked" in sweep.stdout, (text, sweep)
        assert "Traceback" not in sweep.stderr and "debug output" not in sweep.stdout, sweep
        kind, said = outcome(post(claude_payload(box / "x.ts", [(1, ["+console.log(1);"])], f"cfg-{len(text)}"), box))
        assert kind == "context" and "invalid clean-code configuration" in said and reason in said, (text, said)


def test_a_valid_configuration_is_accepted_whether_or_not_tree_sitter_is_installed():
    box = Path(tempfile.mkdtemp())
    (box / ".clean-code.json").write_text('{"disableRules": ["flag-argument", "debug-output"], "maxArguments": 6,'
                                          ' "maxCommentRatio": 0.5, "allowTodo": true, "ignore": [], "allowConsole": []}')
    (box / "x.ts").write_text("console.log(1);\n// TODO: later\n")
    sweep = subprocess.run([sys.executable, str(ENTRY), "files", "x.ts"], capture_output=True, text=True,
                           cwd=str(box), env={"PATH": "/usr/bin:/bin"})
    assert (sweep.returncode, sweep.stdout.strip()) == (0, "clean"), sweep
    (box / ".clean-code.json").write_text("{}")
    assert cc.Config.load(box).max_arguments == 5


def test_the_configuration_is_the_one_at_the_project_root():
    outer = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    (outer / ".clean-code.json").write_text('{"ignore": ["**/legacy/**"]}')
    (outer / "legacy").mkdir()
    (outer / "legacy/old.ts").write_text("console.log(1);\n")
    (outer / "src/deep").mkdir(parents=True)

    def sweep(cwd: Path, target: str):
        return subprocess.run([sys.executable, str(ENTRY), "files", target], capture_output=True, text=True,
                              cwd=str(cwd), env={"PATH": "/usr/bin:/bin"})

    assert sweep(outer / "src/deep", "../../legacy/old.ts").stdout.strip() == "clean", "a subdirectory uses the root config"
    link = outer.parent / (outer.name + "-link")
    link.symlink_to(outer)
    assert sweep(link / "src", "../legacy/old.ts").stdout.strip() == "clean", "a symlinked path uses the same config"
    inner = outer / "vendor-repo"
    subprocess.run(["git", "init", "-q", str(inner)], check=True)
    (inner / "legacy").mkdir()
    (inner / "legacy/old.ts").write_text("console.log(1);\n")
    assert "debug output" in sweep(inner, "legacy/old.ts").stdout, "a nested repository has its own root, not the outer config"


DEFECTS = [
    ("ts", "const a = b as any;", "loose type"),
    ("ts", "console.log('debug');", "debug output"),
    ("ts", "const v = maybe!.value;", "assertion"),
    ("ts", "// @ts-ignore", "silenced"),
    ("ts", "// TODO: fix later", "TODO"),
    ("ts", "// Added validation here", "narrat"),
    ("ts", "// NEVER call this directly", "CAPS"),
    ("ts", "// const dead: number = 1;", "commented-out"),
    ("ts", 'const apiKey = "sk_live_abcdefghijklmnop";', "secret"),
    ("ts", "it.skip('x', () => {});", "test"),
    ("py", "try:\n    run()\nexcept ValueError:\n    pass", "empty catch"),
]


def test_recall_on_the_edited_line():
    """Scoping must never hide a defect the edit itself introduced."""
    box = Path(tempfile.mkdtemp())
    for i, (ext, bad, needle) in enumerate(DEFECTS):
        host = "\n".join(f"v{j} = {j}" if ext == "py" else f"export const v{j}: number = {j};" for j in range(300))
        path = box / f"host{i}.{ext}"
        path.write_text(host + "\n" + bad + "\n")
        proc = post(claude_payload(path, [(301, ["+" + l for l in bad.split("\n")])], f"recall{i}"), box)
        assert needle.lower() in proc.stderr.lower(), f"{bad!r} went unreported: {proc.stderr!r}"


def test_a_banner_on_the_edited_line_is_reported_and_the_file_left_alone():
    box = Path(tempfile.mkdtemp())
    path = box / "banner.ts"
    path.write_text("export const a: number = 1;\n// ---------- section ----------\n")
    proc = post(claude_payload(path, [(1, [" export const a: number = 1;", "+// ---------- section ----------"])], "banner1"), box)
    assert proc.returncode == 2 and "banner comment: delete: L2" in proc.stderr, proc.stderr
    assert "----" in path.read_text()


def test_generated_files_are_not_linted():
    go = "// Code generated by protoc-gen-go. DO NOT EDIT.\n// NOTE: An accurate time signal IS NOT required.\nvar x = y;\n"
    assert rules_for("api.pb.js", go) == set()
    hand = "// Hand written helper.\nconsole.log(1);\n"
    assert "debug-output" in rules_for("hand.js", hand)


def test_prose_is_not_commented_code():
    assert "commented-code" not in rules_for("r1.rb", "# return a 404 response.\nx = 1\n")
    assert "commented-code" not in rules_for("r2.rb", "# class scope.\nx = 1\n")
    assert "commented-code" not in rules_for("r3.py", "# import the module for parsing dates\nx = 1\n")
    assert "commented-code" in rules_for("r4.py", "# return x\ny = 1\n")
    assert "commented-code" in rules_for("r5.ts", "// return buildUser(row);\nconst a: number = 1;\n")


def test_conditional_skip_is_a_platform_guard():
    assert "skipped-test" in rules_for("s1.py", '@unittest.skip("failing buildbots")\ndef test_x(): pass\n')
    assert "skipped-test" in rules_for("s2.py", '@pytest.mark.skip(reason="broken")\ndef test_x(): pass\n')
    assert "skipped-test" not in rules_for("s3.py", '@unittest.skipIf(sys.platform == "win32", "posix only")\ndef test_x(): pass\n')
    assert "skipped-test" not in rules_for("s4.py", '@unittest.skipUnless(WIN_VER, "needs XP")\ndef test_x(): pass\n')
    assert "skipped-test" not in rules_for("s5.py", '@pytest.mark.skipif(sys.version_info < (3, 8), reason="3.8+")\ndef test_x(): pass\n')


def test_focused_test_is_not_a_method_call():
    assert "skipped-test" in rules_for("f1.ts", 'fit("focused", () => {});\n')
    assert "skipped-test" not in rules_for("f2.py", "m = model.fit(data)\n")
    assert "skipped-test" not in rules_for("f3.ts", 'test.skip(browserName === "firefox", "flaky");\n')
    assert "skipped-test" in rules_for("f4.ts", 'it.skip("rate limits", async () => {});\n')


def test_every_rule_is_registered_once_with_a_label():
    ids = [r.id for r in cc.RULES]
    assert len(ids) == len(set(ids)), "duplicate rule id"
    assert set(ids) == set(cc.BY_ID), "BY_ID out of step with RULES"
    for r in cc.RULES:
        assert r.label and r.message, r.id
        assert r.label != r.message, f"{r.id}: label should be the short form"


def test_a_rule_only_runs_on_its_languages():
    assert "weak-type" in rules_for("lang.ts", "const a = b as any;\n")
    assert "weak-type" not in rules_for("lang.go", "var a any = b\n")
    assert "closing-brace-comment" not in rules_for("lang.py", "x = 1  # end of thing\n")


def test_rules_and_explain_are_queryable():
    listing = subprocess.run([sys.executable, str(ENTRY), "rules"], capture_output=True, text=True)
    assert listing.returncode == 0 and "weak-type" in listing.stdout
    good = subprocess.run([sys.executable, str(ENTRY), "explain", "weak-type"], capture_output=True, text=True)
    assert good.returncode == 0 and "Write the real type" in good.stdout
    bad = subprocess.run([sys.executable, str(ENTRY), "explain", "nope"], capture_output=True, text=True)
    assert bad.returncode == 1


def test_ast_rules_register_only_when_tree_sitter_is_present():
    from clean_code import ast_rules
    ast_ids = {"too-many-arguments", "flag-argument"}
    registered = ast_ids & set(cc.BY_ID)
    assert registered == (ast_ids if ast_rules.AVAILABLE else set())


def ast_rules_for(name: str, text: str, edited=None) -> dict:
    path = ROOT / name
    path.write_text(text)
    return {v.rule: (v.hits, v.incomplete) for v in cc.check_file(path, CFG, ROOT, edited)
            if v.rule in ("too-many-arguments", "flag-argument")}


def test_too_many_arguments_counts_what_every_caller_must_pass():
    if not __import__("clean_code.ast_rules", fromlist=["AVAILABLE"]).AVAILABLE:
        return
    flagged = {
        "wide.ts": "export function go(a: A, b: B, c: C, d: D, e: E): void {}\n",
        "ctor.ts": "class S { constructor(a: A, b: B, c: C, d: D, e: E) {} }\n",
        "method.js": "class S { go(a, b, c, d, e) {} }\n",
        "arrow.ts": "export const go = (a: A, b: B, c: C, d: D, e: E) => a;\n",
        "wide.py": "def go(a, b, c, d, e):\n    pass\n",
        "keyword_only.py": "def go(a, b, *, c, d, e):\n    pass\n",
        "method.py": "class S:\n    def go(self, a, b, c, d, e):\n        pass\n",
        "lambda.py": "go = lambda a, b, c, d, e: a\n",
    }
    for name, text in flagged.items():
        assert "too-many-arguments" in ast_rules_for(name, text), name
    clean = {
        "narrow.ts": "export function go(a: A, b: B, c: C, d: D): void {}\n",
        "defaults.ts": "export function go(a: A, b: B, c: C, d: D, e = 1, f?: F): void {}\n",
        "rest.ts": "export function go(a: A, b: B, c: C, d: D, ...rest: E[]): void {}\n",
        "options.ts": "export function go({ a, b, c, d, e }: Options): void {}\n",
        "this.ts": "export function go(this: Window, a: A, b: B, c: C, d: D): void {}\n",
        "callback.ts": "items.reduce((a, b, c, d, e) => a, 0);\n",
        "override.ts": "class S extends B { override go(a: A, b: B, c: C, d: D, e: E) {} }\n",
        "defaults.py": "def go(name, label, message, languages=None, whole_file=False):\n    pass\n",
        "variadic.py": "def go(a, b, c, d, *args, **kwargs):\n    pass\n",
        "positional.py": "def go(a, b, /, c, d):\n    pass\n",
        "callback.py": "items.sort(key=lambda a, b, c, d, e: a)\n",
    }
    for name, text in clean.items():
        assert "too-many-arguments" not in ast_rules_for(name, text), name


def test_a_boolean_parameter_is_a_flag_only_where_it_picks_a_branch():
    if not __import__("clean_code.ast_rules", fromlist=["AVAILABLE"]).AVAILABLE:
        return
    flagged = {
        "branch.ts": "export function render(deep: boolean) {\n  if (deep) { walk(); }\n  return draw();\n}\n",
        "ternary.js": "export function render(deep = false) {\n  return deep ? walk() : draw();\n}\n",
        "branch.py": "def render(compact: bool):\n    if compact:\n        return small()\n    return large()\n",
        "default.py": "def check(strict=False):\n    return exact() if strict else loose()\n",
        "handler.ts": "function handleToggle(open: boolean) {\n  if (open) { track(); }\n  save(open);\n}\n",
    }
    for name, text in flagged.items():
        assert "flag-argument" in ast_rules_for(name, text), name
    clean = {
        "data.ts": "export function remember(checked: boolean) {\n  return save(checked);\n}\n",
        "stored.py": "class A:\n    def keep(self, flag: bool):\n        self.flag = flag\n",
        "setter.ts": "class A { setVisible(visible: boolean): void { if (visible) show(); } }\n",
        "options.ts": "export function run({ strict }: { strict: boolean }) {\n  if (strict) { exact(); }\n}\n",
        "callback.tsx": "export const P = () => <input onChange={(checked: boolean) => { if (checked) save(); }} />;\n",
        "inline.ts": "rows.filter((keep: boolean) => { if (keep) { return true; } return false; });\n",
        "override.ts": "class S extends B { override show(open: boolean) { if (open) run(); } }\n",
    }
    for name, text in clean.items():
        assert "flag-argument" not in ast_rules_for(name, text), name
    [message] = [r.message for r in cc.RULES if r.id == "flag-argument"]
    assert "split" not in message.lower() or "if" in message.lower()


def test_an_ast_rule_does_not_judge_lines_its_parser_could_not_read():
    if not __import__("clean_code.ast_rules", fromlist=["AVAILABLE"]).AVAILABLE:
        return
    text = "const rows = await sql<{ n: number }[]>`select 1`;\nexport function go(a, b, c, d, e) {}\n"
    found = ast_rules_for("gap-ast.ts", text, {1})
    assert found["too-many-arguments"][1] and found["flag-argument"][1], found
    assert found.get("too-many-arguments", ([], ""))[0] == [], found
    assert ast_rules_for("gap-ast.ts", text, {2})["too-many-arguments"][0], "a function the parser did read is judged"


def test_an_unknown_command_is_an_error():
    proc = subprocess.run([sys.executable, str(ENTRY), "chek"], capture_output=True, text=True)
    assert proc.returncode == 1 and "unknown command chek" in proc.stderr, proc


def test_prose_may_quote_a_setting_it_does_not_change():
    doc = {"tool_name": "Write", "tool_input": {"file_path": "README.md",
           "content": 'Set `"strict": false` only when you mean it, and `"disableRules": []` by default.'}}
    assert cc.pre_check(doc, ROOT) is None
    cfg = {"tool_name": "Write", "tool_input": {"file_path": "tsconfig.json", "content": '{"strict": false}'}}
    assert cc.pre_check(cfg, ROOT)
    src = {"tool_name": "Edit", "tool_input": {"file_path": "a.ts", "old_string": "x", "new_string": "// eslint-disable"}}
    assert cc.pre_check(src, ROOT)


def test_a_regex_literal_is_not_a_comment():
    def comments(text, ext=".ts"):
        return cc.split_comments(text, ext)[1]

    assert comments("if (!/^[^:/?#]+:\\/\\//.test(uri)) { run(); }") == []
    assert comments('const s = t.replace(/[/\\\\]+/g, "-");') == []
    assert len(comments("const a = 1 / 2; // real comment")) == 1
    assert len(comments("const r = /ab+c/; // after a regex")) == 1
    assert len(comments("const d = total / count; // ratio")) == 1


def test_an_apostrophe_in_prose_does_not_open_a_string():
    jsx = "<p>add to fans' library</p>\n{/* Quick Stats */}\n// TODO: later\n"
    assert reading(jsx, ".tsx") == ([(2, "/* Quick Stats */"), (3, "// TODO: later")] if source_module.get_parser else "abstained")
    code = "const a = 'real string'; // after\nconst b = \"don't\"; // inside quotes\n"
    assert len(cc.split_comments(code, ".ts")[1]) == 2
    py = 'x = "a"  # comment\ns = "it\'s"  # another\n'
    assert len(cc.split_comments(py, ".py")[1]) == 2


def test_a_template_literal_may_still_span_lines():
    text = "const q = `\n  SELECT 1\n`; // after the template\n"
    found = cc.split_comments(text, ".ts")[1]
    assert len(found) == 1 and "after the template" in found[0].text


FOREIGN_SETTINGS = {
    "theme": "dark",
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "somebody-elses-hook"}]}]},
}


def run_installer(home: Path, *args: str) -> tuple:
    """The installer in-process against a throwaway home; never the real one."""
    import contextlib
    import io
    from clean_code import install as inst
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = inst.main(list(args), home=home)
    return code, out.getvalue() + err.getvalue()


def snapshot(home: Path) -> dict:
    return {str(p.relative_to(home)): (p.read_bytes() if p.is_file() else "dir") for p in sorted(home.rglob("*"))}


def fresh_home(name: str = "home") -> Path:
    home = Path(tempfile.mkdtemp()) / name
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    return home


def hook_groups(settings: Path, event: str) -> list:
    return json.loads(settings.read_text()).get("hooks", {}).get(event, [])


def ours(home: Path, settings: Path, event: str) -> list:
    from clean_code.install import group_is_ours
    return [g for g in hook_groups(settings, event) if group_is_ours(g, home / ".clean-code/clean_check.py")]


def test_installer_merges_and_removes_only_its_own_hooks():
    home = fresh_home()
    settings = home / ".claude/settings.json"
    settings.write_text(json.dumps(FOREIGN_SETTINGS))
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    assert len(ours(home, settings, "PreToolUse")) == 1 and len(ours(home, settings, "PostToolUse")) == 1
    assert json.loads(settings.read_text())["theme"] == "dark"
    assert any("somebody-elses-hook" in g["hooks"][0]["command"] for g in hook_groups(settings, "PreToolUse"))
    assert list((home / ".claude").glob("settings.json.clean-code-*.bak"))
    assert (home / ".clean-code/clean_code/rules.py").is_file() and (home / ".claude/skills/clean-code/SKILL.md").is_file()
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    assert len(ours(home, settings, "PreToolUse")) == 1, "installing twice must not stack hooks"
    assert run_installer(home, "--claude", "--uninstall")[0] == 0
    assert ours(home, settings, "PreToolUse") == [] and json.loads(settings.read_text())["theme"] == "dark"
    assert any("somebody-elses-hook" in g["hooks"][0]["command"] for g in hook_groups(settings, "PreToolUse"))
    assert not (home / ".clean-code").exists() and not (home / ".claude/skills/clean-code").exists()
    assert run_installer(home, "--claude", "--uninstall")[0] == 0, "uninstalling twice is not an error"


def test_both_agents_in_a_home_with_spaces_install_hooks_that_run():
    home = fresh_home("home with spaces")
    code, said = run_installer(home, "--no-parser")
    assert code == 0 and "Claude Code: installed" in said and "Codex: installed" in said, said
    assert "React, TSX and JSX checking: OFF" in said and "not checked" in said, said
    [group] = ours(home, home / ".codex/hooks.json", "PostToolUse")
    command = group["hooks"][0]["command"]
    target = home / "project/a.ts"
    target.parent.mkdir()
    target.write_text("console.log(1);\n")
    payload = json.dumps(claude_payload(target, [(1, ["+console.log(1);"])], "spaces"))
    proc = subprocess.run(command, shell=True, input=payload, capture_output=True, text=True, cwd=str(target.parent))
    assert proc.returncode == 2 and "debug output: L1" in proc.stderr, proc


def test_a_malformed_settings_file_changes_nothing():
    for broken in (".claude/settings.json", ".codex/hooks.json"):
        home = fresh_home()
        assert run_installer(home, "--no-parser")[0] == 0
        (home / broken).write_text('{"hooks": {')
        before = snapshot(home)
        for args in (("--no-parser",), ("--uninstall",)):
            code, said = run_installer(home, *args)
            assert code == 1 and "is not valid JSON" in said and "Nothing was changed" in said, said
            assert snapshot(home) == before, broken


def test_a_permission_failure_changes_nothing():
    if os.geteuid() == 0:
        return
    home = fresh_home()
    (home / ".claude/settings.json").write_text("{}")
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    before = snapshot(home)
    (home / ".claude").chmod(0o555)
    try:
        code, said = run_installer(home, "--claude", "--no-parser")
    finally:
        (home / ".claude").chmod(0o755)
    assert code == 1 and "Nothing was changed" in said, said
    assert snapshot(home) == before


def test_a_failure_midway_through_the_swap_puts_everything_back():
    from clean_code import install as inst
    home = fresh_home()
    assert run_installer(home, "--no-parser")[0] == 0
    before = snapshot(home)
    real, calls = inst.os.replace, []

    def flaky(source, target):
        calls.append(target)
        if len(calls) == 9:
            raise OSError(28, "No space left on device", str(target))
        real(source, target)

    inst.os.replace = flaky
    try:
        code, said = run_installer(home, "--no-parser")
    finally:
        inst.os.replace = real
    assert code == 1 and "the old state is back" in said, said
    assert snapshot(home) == before


def test_an_interrupted_run_is_recovered_by_the_next():
    home = fresh_home()
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    core = home / ".clean-code"
    os.replace(core, home / ".clean-code.clean-code-old")
    (home / ".clean-code.clean-code-new").mkdir()
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    assert (core / "clean_check.py").is_file()
    assert not (home / ".clean-code.clean-code-old").exists() and not (home / ".clean-code.clean-code-new").exists()


def test_a_legacy_install_is_migrated_and_foreign_stop_hooks_stay():
    home = fresh_home()
    legacy_core = home / ".clean-code/clean_code"
    legacy_core.mkdir(parents=True)
    (legacy_core / "report.py").write_text("def autofix(path):\n    path.write_text('')\n")
    (home / ".clean-code/clean_check.py").write_text("")
    check = home / ".clean-code/clean_check.py"
    legacy = {"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": f'python3 "{check}" stop'}]},
                 {"hooks": [{"type": "command", "command": "somebody-elses-stop"}]}],
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": f'python3 "{check}" post'}]}]}}
    (home / ".claude/settings.json").write_text(json.dumps(legacy))
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    stops = hook_groups(home / ".claude/settings.json", "Stop")
    assert [g["hooks"][0]["command"] for g in stops] == ["somebody-elses-stop"]
    assert len(ours(home, home / ".claude/settings.json", "PostToolUse")) == 1
    assert "autofix" not in (legacy_core / "report.py").read_text()


def test_a_partial_install_is_repaired_and_backups_do_not_pile_up():
    home = fresh_home()
    assert run_installer(home, "--no-parser")[0] == 0
    import shutil as _shutil
    _shutil.rmtree(home / ".clean-code")
    for _ in range(5):
        assert run_installer(home, "--claude", "--no-parser")[0] == 0
    assert (home / ".clean-code/clean_check.py").is_file()
    assert len(list((home / ".claude").glob("settings.json.clean-code-*.bak"))) == 3


def test_installing_one_agent_moves_every_hook_that_runs_the_shared_core():
    home = fresh_home()
    assert run_installer(home, "--no-parser")[0] == 0
    codex = home / ".codex/hooks.json"
    stale = codex.read_text().replace(json.dumps(sys.executable)[1:-1], "/gone/venv/bin/python")
    codex.write_text(stale)
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    [group] = ours(home, codex, "PostToolUse")
    assert "/gone/" not in group["hooks"][0]["command"] and sys.executable in group["hooks"][0]["command"]
    assert not (home / ".agents/skills/clean-code.clean-code-new").exists()


def test_removing_one_agent_keeps_the_core_the_other_still_uses():
    home = fresh_home()
    assert run_installer(home, "--no-parser")[0] == 0
    assert run_installer(home, "--claude", "--uninstall")[0] == 0
    assert (home / ".clean-code/clean_check.py").is_file() and ours(home, home / ".codex/hooks.json", "PostToolUse")
    assert run_installer(home, "--codex", "--uninstall")[0] == 0
    assert not (home / ".clean-code").exists()


def test_a_fresh_install_says_whether_tsx_is_checked_and_means_it():
    home = fresh_home("react home")
    code, said = run_installer(home, "--claude")
    assert code == 0, said
    [group] = ours(home, home / ".claude/settings.json", "PostToolUse")
    command = group["hooks"][0]["command"]
    page = home / "app/page.tsx"
    page.parent.mkdir()
    text = "export default function Page() {\n  return <p>{console.log(1)}</p>;\n}\n"
    page.write_text(text)
    payload = json.dumps(claude_payload(page, [(2, ["+" + text.split("\n")[1]])], "react"))
    proc = subprocess.run(command, shell=True, input=payload, capture_output=True, text=True, cwd=str(page.parent))
    if "React, TSX and JSX checking: on" in said:
        assert (home / ".clean-code/venv/bin/python").is_file() and "venv/bin/python" in command
        assert proc.returncode == 2 and "debug output: L2" in proc.stderr, proc
    else:
        assert "React, TSX and JSX checking: OFF" in said and "could not set up tree-sitter" in said, said
        assert proc.returncode == 0 and "not checked" in proc.stdout, proc


def next_like_project() -> Path:
    """A client, a lib that reaches a driver, and a server action, which is the shape that misleads."""
    box = Path(tempfile.mkdtemp())
    (box / "lib").mkdir()
    (box / "actions").mkdir()
    (box / "components").mkdir()
    (box / "lib/redis.ts").write_text('import Redis from "ioredis";\nexport const redis = new Redis();\n')
    (box / "lib/presence.ts").write_text('import { redis } from "./redis";\nexport const TTL_MS = 300000;\nexport type Presence = { at: number };\n')
    (box / "actions/read.ts").write_text('"use server";\nimport { redis } from "../lib/redis";\nexport async function read() { return redis.keys("x"); }\n')
    return box


def boundary_rules(box: Path, body: str) -> set[str]:
    path = box / "components/panel.ts"
    path.write_text(body)
    return {v.rule for v in cc.check_file(path, cc.Config.load(box), box)}


def test_a_client_may_not_reach_a_server_only_package():
    box = next_like_project()
    two_hops = '"use client";\nimport { TTL_MS } from "../lib/presence";\nexport function P() { return TTL_MS; }\n'
    assert "client-bundles-server-code" in boundary_rules(box, two_hops), "two hops is the real shape"

    direct = '"use client";\nimport Redis from "ioredis";\nexport function P() { return Redis; }\n'
    assert "client-bundles-server-code" in boundary_rules(box, direct)


def test_the_boundary_rule_leaves_correct_code_alone():
    box = next_like_project()
    typed = '"use client";\nimport type { Presence } from "../lib/presence";\nexport function P(x: Presence) { return x; }\n'
    assert "client-bundles-server-code" not in boundary_rules(box, typed), "a type import is erased"

    action = '"use client";\nimport { read } from "../actions/read";\nexport function P() { return read; }\n'
    assert "client-bundles-server-code" not in boundary_rules(box, action), "a server action is an RPC boundary"

    plain = '"use client";\nimport { useState } from "react";\nexport function P() { return useState(0); }\n'
    assert "client-bundles-server-code" not in boundary_rules(box, plain)

    server = '"use server";\nimport { redis } from "../lib/redis";\nexport async function go() { return redis; }\n'
    path = box / "actions/other.ts"
    path.write_text(server)
    assert "client-bundles-server-code" not in {v.rule for v in cc.check_file(path, cc.Config.load(box), box)}, \
        "a server module may import whatever it likes"


READ_ONLY_FIXTURES = {
    "lf.ts": b"// ---------- helpers ----------\n// map errors \xe2\x80\x94 onto the envelope\nif (x) {\n  run();\n} // end if\n",
    "crlf.ts": b"// ---------- helpers ----------\r\nconst a = 1;\r\nif (a) {\r\n  run();\r\n} // end\r\n",
    "utf8.ts": "// caf\u00e9 \u2192 na\u00efve \u00b7 r\u00e9sum\u00e9\nconst s: string = \"\u2014\";\n".encode(),
    "marker.ts": b'const marker = "} // end";\n',
    "sql.py": 'SQL = """SELECT a \u2014 b FROM table"""\n'.encode(),
    "latin1.ts": b"// ---------- caf\xe9 ----------\nconsole.log(1);\n",
}


def test_post_mode_never_writes_the_file():
    box = Path(tempfile.mkdtemp())
    for name, data in READ_ONLY_FIXTURES.items():
        path = box / name
        path.write_bytes(data)
        before = path.stat().st_mtime_ns
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        post(claude_payload(path, [(1, ["+" + l for l in text.split("\n")])], f"read-only-{name}"), box)
        assert path.read_bytes() == data, name
        assert path.stat().st_mtime_ns == before, name


def test_post_mode_leaves_a_symlink_and_its_target_alone():
    box = Path(tempfile.mkdtemp())
    target = box / "target.ts"
    data = b"// ---------- helpers ----------\nif (a) {\n  run();\n} // end\n"
    target.write_bytes(data)
    link = box / "link.ts"
    link.symlink_to(target)
    text = data.decode()
    post(claude_payload(link, [(1, ["+" + l for l in text.split("\n")])], "read-only-link"), box)
    assert link.is_symlink() and target.read_bytes() == data


LEGACY = "console.log('legacy');"


def scope(path: Path, text: str, hunks: list) -> "set[int] | None":
    path.write_text(text)
    [(_, lines)] = cc.edited_lines(claude_payload(path, hunks))
    return lines


def test_scope_of_insertion_replacement_and_multiline_replacement():
    path = ROOT / "scope.ts"
    assert scope(path, f"{LEGACY}\nconst a = 1;\nconst c = 3;\nconst b = 2;\n",
                 [(1, [f" {LEGACY}", " const a = 1;", "+const c = 3;", " const b = 2;"])]) == {3}
    assert scope(path, f"{LEGACY}\nconst a = 10;\nconst b = 2;\n",
                 [(1, [f" {LEGACY}", "-const a = 1;", "+const a = 10;", " const b = 2;"])]) == {2}
    assert scope(path, f"{LEGACY}\nconst x = 1;\nconst y = 2;\nconst z = 3;\n{LEGACY}\n",
                 [(1, [f" {LEGACY}", "-const a = 1;", "-const b = 2;", "+const x = 1;", "+const y = 2;", "+const z = 3;", f" {LEGACY}"])]) == {2, 3, 4}


def test_a_deletion_introduces_no_lines_and_a_blank_line_only_itself():
    path = ROOT / "scope-delete.ts"
    assert scope(path, f"{LEGACY}\nconst b = 2;\n{LEGACY}\n", [(1, [f" {LEGACY}", "-const a = 1;", " const b = 2;"])]) == set()
    assert scope(path, f"{LEGACY}\nconst a = 1;\n\nconst b = 2;\n{LEGACY}\n", [(2, [" const a = 1;", "+", " const b = 2;"])]) == {3}


def test_identical_legacy_text_above_or_below_is_not_the_edit():
    path = ROOT / "scope-dupes.ts"
    above = f"{LEGACY}\nconst a = 1;\n{LEGACY}\nconst b = 2;\n{LEGACY}\n"
    assert scope(path, above, [(2, [" const a = 1;", f"+{LEGACY}", " const b = 2;"])]) == {3}
    below = f"{LEGACY}\nconst a = 1;\n{LEGACY}\n"
    assert scope(path, below, [(1, [f"+{LEGACY}", " const a = 1;", f" {LEGACY}"])]) == {1}


def test_identical_new_lines_at_two_places_are_each_the_edit():
    path = ROOT / "scope-twice.ts"
    text = "const a = 1;\nconst c = 3;\nconst b = 2;\nconst c = 3;\nconst d = 4;\nconst c = 3;\n"
    hunks = [(1, [" const a = 1;", "+const c = 3;", " const b = 2;"]), (4, ["+const c = 3;", " const d = 4;", " const c = 3;"])]
    assert scope(path, text, hunks) == {2, 4}


def test_whitespace_different_duplicates_are_different_lines():
    path = ROOT / "scope-indent.ts"
    assert scope(path, "  console.log('x');\nconsole.log('x');\n", [(1, ["   console.log('x');", "+console.log('x');"])]) == {2}


def test_a_file_moved_since_the_edit_makes_scope_unavailable():
    path = ROOT / "scope-moved.ts"
    formatted = "const a = 1;\n\nconst c = 3;\n"
    assert scope(path, formatted, [(1, [" const a = 1;", "+const c = 3;"])]) is None


def test_a_payload_without_coordinates_makes_scope_unavailable():
    path = ROOT / "scope-bare.ts"
    path.write_text(f"{LEGACY}\n")
    bare = {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": "x", "new_string": LEGACY}}
    assert cc.edited_lines(bare) == [(path, None)]


def test_a_created_file_is_new_on_every_line():
    path = ROOT / "scope-new.ts"
    text = "const a = 1;\nconst b = 2;\n"
    path.write_text(text)
    created = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": text},
               "tool_response": {"type": "create", "filePath": str(path), "content": text, "structuredPatch": []}}
    assert cc.edited_lines(created) == [(path, {1, 2, 3})]
    unchanged = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": text},
                 "tool_response": {"type": "update", "filePath": str(path), "content": text, "structuredPatch": []}}
    assert cc.edited_lines(unchanged) == [(path, set())]


REAL_CLAUDE_EDIT = {
    "hook_event_name": "PostToolUse", "tool_name": "Edit",
    "tool_input": {"file_path": "f.ts", "old_string": "const b = 2;", "new_string": "const b = 20;\nconst bb = 21;", "replace_all": False},
    "tool_response": {
        "filePath": "f.ts", "oldString": "const b = 2;", "newString": "const b = 20;\nconst bb = 21;",
        "originalFile": "const a = 1;\nconst b = 2;\nconst c = 3;\n",
        "structuredPatch": [{"oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 4,
                             "lines": [" const a = 1;", "-const b = 2;", "+const b = 20;", "+const bb = 21;", " const c = 3;"]}],
        "userModified": False, "replaceAll": False},
}


def test_the_payload_claude_code_really_sends_is_scoped():
    box = Path(tempfile.mkdtemp())
    (box / "f.ts").write_text("const a = 1;\nconst b = 20;\nconst bb = 21;\nconst c = 3;\n")
    assert cc.edited_lines({**REAL_CLAUDE_EDIT, "cwd": str(box)}) == [(box / "f.ts", {2, 3})]


def codex_payload(patch: str, cwd: Path) -> dict:
    return {"tool_name": "apply_patch", "cwd": str(cwd), "tool_input": {"command": patch}}


CODEX_UPDATE = """*** Begin Patch
*** Update File: svc.ts
@@
 const a = 1;
+console.log('new');
 const b = 2;
*** End Patch"""


def test_codex_hunks_are_located_by_their_context():
    box = Path(tempfile.mkdtemp())
    (box / "svc.ts").write_text("console.log('new');\nconst z = 0;\nconst a = 1;\nconsole.log('new');\nconst b = 2;\n")
    assert cc.edited_lines(codex_payload(CODEX_UPDATE, box)) == [(box / "svc.ts", {4})]
    deletion = "*** Begin Patch\n*** Update File: svc.ts\n@@\n const z = 0;\n-const y = 9;\n const a = 1;\n*** End Patch"
    assert cc.edited_lines(codex_payload(deletion, box)) == [(box / "svc.ts", set())]
    added = "*** Begin Patch\n*** Add File: fresh.ts\n+const a = 1;\n+const b = 2;\n*** Delete File: gone.ts\n*** End Patch"
    (box / "fresh.ts").write_text("const a = 1;\nconst b = 2;\n")
    assert cc.edited_lines(codex_payload(added, box)) == [(box / "fresh.ts", {1, 2})]


def test_ambiguous_codex_context_makes_scope_unavailable_unless_anchored():
    box = Path(tempfile.mkdtemp())
    block = "const a = 1;\nconsole.log('new');\nconst b = 2;\n"
    (box / "svc.ts").write_text("function first() {\n" + block + "}\nfunction second() {\n" + block + "}\n")
    assert cc.edited_lines(codex_payload(CODEX_UPDATE, box)) == [(box / "svc.ts", None)]
    anchored = CODEX_UPDATE.replace("@@\n", "@@ function second() {\n")
    assert cc.edited_lines(codex_payload(anchored, box)) == [(box / "svc.ts", {8})]


def test_legacy_defects_around_an_edit_stay_legacy():
    box = Path(tempfile.mkdtemp())
    path = box / "around.ts"
    path.write_text(f"{LEGACY}\nconst a = 1;\nconst x = y as any;\nconst b = 2;\n{LEGACY}\n")
    proc = post(claude_payload(path, [(2, [" const a = 1;", "+const x = y as any;", " const b = 2;"])], "around"), box)
    assert proc.returncode == 2 and "loose type: write the real type: L3" in proc.stderr, proc.stderr
    assert "debug output" not in proc.stderr, proc.stderr


def test_repeating_a_legacy_defect_reports_only_the_new_copy():
    box = Path(tempfile.mkdtemp())
    path = box / "repeat.ts"
    path.write_text(f"{LEGACY}\nconst a = 1;\n{LEGACY}\nconst b = 2;\n{LEGACY}\n")
    proc = post(claude_payload(path, [(2, [" const a = 1;", f"+{LEGACY}", " const b = 2;"])], "repeat"), box)
    assert proc.returncode == 2 and "debug output: L3\n" in proc.stderr + "\n", proc.stderr


def test_removing_a_defect_reports_nothing():
    box = Path(tempfile.mkdtemp())
    path = box / "removed.ts"
    path.write_text(f"{LEGACY}\nconst a = 1;\n{LEGACY}\n")
    proc = post(claude_payload(path, [(1, [f" {LEGACY}", " const a = 1;", "-const x = y as any;", f" {LEGACY}"])], "removed"), box)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


JS_FIXTURE = r"""const a = 'it\'s // not'; // c1
const b = "say \"hi\" /* not */"; // c2
const r = /[/*]+\/\/x/g; // c3
const d = total / count / 2; // c4
const u = "https://example.com/path"; // c5
const t = `// not ${a} /* not */`; // c6
const m = `
  // not a comment
`; /* c7 */
const s = `a ${ /* c8 */ b } c`;
const n = `x ${ a ? `inner // not ${ b /* c9 */ }` : "" } y`; // c10
const w = `${`${`deep`}`}`; // c11
/*
 * c12
 */
const url = `https://x.y/${a}`; // c13
const p = [
  /^(.)\1+$/, // c14
  /^(123)+/, // c15
];
const e = h.replace(/</g, "&lt;").replace(/>/g, "&gt;"); // c16
const k = ready && !failed
  /* c17 */
  ? 1
  : 2;
"""


JS_COMMENTS = [(1, "// c1"), (2, "// c2"), (3, "// c3"), (4, "// c4"), (5, "// c5"), (6, "// c6"), (9, "/* c7 */"),
               (10, "/* c8 */"), (11, "/* c9 */"), (11, "// c10"), (12, "// c11"), (13, "/*\n * c12\n */"), (16, "// c13"),
               (18, "// c14"), (19, "// c15"), (21, "// c16"), (23, "/* c17 */")]


TSX_FIXTURE = """export function P({ items }: { items: string[] }) {
  return (
    <div title="don't // not" data-x='a "b" c'>
      <p>it's {/* c1 */} don't</p>
      {/* c2 */}
      {items.map((i) => <li key={i}>{i /* c3 */}</li>)}
      <a href="//cdn.example.com/x">docs at https://example.com/x</a>
    </div>
  ); // c4
}
export const Q = (props) => <Item {...props} />; // c5
"""


TSX_COMMENTS = [(4, "/* c1 */"), (5, "/* c2 */"), (6, "/* c3 */"), (9, "// c4"), (11, "// c5")]


def found(text: str, ext: str) -> list:
    return [(c.line, c.text) for c in cc.split_comments(text, ext)[1]]


def scanned(text: str, ext: str) -> list:
    return [(c.line, c.text) for c in source_module.assemble(text, source_module.scan_comments(text, ext))[1]]


def test_js_comments_are_exact_around_strings_regexes_and_templates():
    for ext in (".ts", ".js", ".mjs", ".cjs"):
        assert found(JS_FIXTURE, ext) == JS_COMMENTS, ext
        assert scanned(JS_FIXTURE, ext) == JS_COMMENTS, ext


def test_jsx_comments_are_exact_with_a_parser():
    if not source_module.get_parser:
        return
    assert found(TSX_FIXTURE, ".tsx") == TSX_COMMENTS
    assert found(TSX_FIXTURE.replace(": { items: string[] }", ""), ".jsx") == TSX_COMMENTS


def test_an_unclosed_quote_does_not_shift_later_lines():
    assert scanned("const s = 'a\\\nb';\n// after\n", ".ts") == [(3, "// after")]
    assert scanned("const a = `x ${b} y\n// inside\n", ".ts") == []


PY_FIXTURE = "\n".join([
    '"""Module docstring."""',
    "import re",
    "S1 = 'it\\'s # not'",
    'S2 = "say \\"# not\\""',
    'SQL = """SELECT a # not',
    'FROM t"""',
    "M = '''multi",
    "line'''  # c1",
    'F = f"{S1} # not {S2!r}"  # c2',
    "def f():",
    '    """Function docstring."""',
    "    return re  # c3",
    "class C:",
    "    '''Class docstring.'''",
    '    x = """not a docstring"""',
    "",
])


def test_python_docstrings_are_comments_and_assigned_strings_are_not():
    assert found(PY_FIXTURE, ".py") == [(1, "Module docstring."), (8, "# c1"), (9, "# c2"), (11, "Function docstring."),
                                        (12, "# c3"), (14, "Class docstring.")]
    code = cc.split_comments(PY_FIXTURE, ".py")[0]
    assert "SELECT a # not" in code[4] and code[10].strip() == "" and 'not a docstring' in code[14]


def test_python_that_does_not_parse_is_reported_unchecked_not_clean():
    try:
        cc.split_comments("def f(:\n    pass\n", ".py")
    except cc.Unparsable as exc:
        assert "line 1" in str(exc)
    else:
        raise AssertionError("broken Python was read as if it parsed")
    box = Path(tempfile.mkdtemp())
    path = box / "broken.py"
    path.write_text("print('x')\ndef f(:\n")
    kind, said = outcome(post(claude_payload(path, [(1, ["+print('x')", "+def f(:"])], "broken"), box))
    assert kind == "context" and "not checked" in said and "debug output" not in said, said
    sweep = subprocess.run([sys.executable, str(ENTRY), "files", str(path)], capture_output=True, text=True, cwd=str(box),
                           env={"PATH": "/usr/bin:/bin"})
    assert sweep.returncode == 1 and "not checked" in sweep.stdout, sweep


def test_a_closing_brace_inside_a_string_is_not_a_comment():
    assert "closing-brace-comment" not in rules_for("brace-string.ts", 'const marker = "} // end";\n')
    assert "closing-brace-comment" in rules_for("brace-comment.ts", "if (x) {\n  run();\n} // end if\n")


def outcome(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    """What the agent receives: exit 2 hands it stderr, exit 0 only JSON additionalContext from stdout."""
    if proc.returncode == 2:
        return "report", proc.stderr
    assert proc.returncode == 0, proc
    if not proc.stdout:
        assert proc.stderr == "", proc.stderr
        return "silent", ""
    context = json.loads(proc.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] in ("PostToolUse", "PostToolUseFailure") and proc.stderr == "", proc
    return "context", context["additionalContext"]


def test_a_checked_clean_edit_says_nothing_and_a_violation_is_reported():
    box = Path(tempfile.mkdtemp())
    clean = box / "clean.ts"
    clean.write_text("const a = 1;\n")
    assert outcome(post(claude_payload(clean, [(1, ["+const a = 1;"])], "matrix-clean"), box)) == ("silent", "")
    dirty = box / "dirty.ts"
    dirty.write_text("console.log(1);\n")
    kind, text = outcome(post(claude_payload(dirty, [(1, ["+console.log(1);"])], "matrix-dirty"), box))
    assert kind == "report" and "debug output: L1" in text, text


def test_an_edit_that_cannot_be_located_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    moved = box / "moved.ts"
    moved.write_text("const a = 1;\n\nconsole.log(1);\n")
    kind, text = outcome(post(claude_payload(moved, [(1, [" const a = 1;", "+console.log(1);"])], "matrix-moved"), box))
    assert kind == "context" and "moved.ts not checked" in text and "could not be located" in text, text
    block = "const a = 1;\nconsole.log('new');\nconst b = 2;\n"
    (box / "svc.ts").write_text(block + block)
    kind, text = outcome(post({**codex_payload(CODEX_UPDATE, box), "session_id": "matrix-codex"}, box))
    assert kind == "context" and "svc.ts not checked" in text, text


def test_unparsable_python_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    path = box / "broken.py"
    path.write_text("print('x')\ndef f(:\n")
    kind, text = outcome(post(claude_payload(path, [(1, ["+print('x')", "+def f(:"])], "matrix-py"), box))
    assert kind == "context" and "broken.py not checked" in text and "not valid Python" in text, text


def test_jsx_without_a_parser_that_accepts_it_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    path = box / "amp.tsx"
    text = "export const A = () => <p>Catalog & Revenue</p>;\n// TODO: real defect\n"
    path.write_text(text)
    kind, said = outcome(post(claude_payload(path, [(1, ["+" + l for l in text.split("\n")])], "matrix-jsx"), box))
    assert kind == "context" and "amp.tsx not checked" in said and "JSX" in said, said


def test_a_malformed_hook_payload_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    for bad in ("not json", "[1, 2]"):
        proc = subprocess.run([sys.executable, str(ENTRY), "post"], input=bad, capture_output=True, text=True,
                              env={"CLAUDE_PROJECT_DIR": str(box), "PATH": "/usr/bin:/bin"}, cwd=str(box))
        kind, said = outcome(proc)
        assert kind == "context" and "not checked" in said and "hook input" in said, said


def test_a_report_carries_the_not_checked_note_for_other_files():
    box = Path(tempfile.mkdtemp())
    (box / "fresh.ts").write_text("console.log(1);\n")
    patch = "*** Begin Patch\n*** Add File: fresh.ts\n+console.log(1);\n*** Update File: gone.ts\n@@\n-a\n+b\n*** End Patch"
    kind, said = outcome(post({**codex_payload(patch, box), "session_id": "matrix-both"}, box))
    assert kind == "report" and "debug output: L1" in said and "gone.ts not checked" in said, said


REAL_CODEX_PATCH = {
    "hook_event_name": "PostToolUse", "model": "gpt-5.6-sol", "permission_mode": "bypassPermissions",
    "tool_name": "apply_patch", "tool_use_id": "call_1", "turn_id": "t1", "session_id": "codex-real",
    "tool_input": {"command": "*** Begin Patch\n*** Update File: {path}\n@@\n-const a = 1;\n+console.log(2);\n*** End Patch"},
    "tool_response": "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nSuccess. Updated the following files:\nM {path}\n",
}


def test_the_payload_codex_really_sends_is_scoped_and_reported():
    box = Path(tempfile.mkdtemp())
    path = box / "f.ts"
    path.write_text("console.log(2);\n")
    payload = json.loads(json.dumps(REAL_CODEX_PATCH).replace("{path}", str(path)))
    assert cc.edited_lines({**payload, "cwd": str(box)}) == [(path, {1})]
    kind, said = outcome(post({**payload, "cwd": str(box)}, box))
    assert kind == "report" and "debug output: L1" in said, said


def reading(text: str, ext: str):
    """The comments one file yields, or "abstained" when no reader can be trusted with it."""
    try:
        return found(text, ext)
    except cc.Unparsable:
        return "abstained"


SOL_FALSE_NEGATIVE = "export const F = () => <p>Run ```bash to start</p>;\n// TODO: real defect\n"
SOL_FALSE_COMMENT = "export const G = () => <p>see https://example.com/TODO</p>;\n"


def test_the_sol_jsx_reproducers_are_read_right_or_not_at_all():
    parser = bool(source_module.get_parser)
    assert reading(SOL_FALSE_NEGATIVE, ".tsx") == ([(2, "// TODO: real defect")] if parser else "abstained")
    assert reading(SOL_FALSE_COMMENT, ".tsx") == ([] if parser else "abstained")


JSX_TEXT = """export function Page({ items }: { items: string[] }) {
  return (
    <>
      <p>It's a fan's page, don't panic.</p>
      <p>Run ```bash``` or `npm i` first</p>
      <p>See https://example.com/a//b and /* not a comment */ here</p>
      <p>Braces {"{"} and {"}"} and &amp; &lt;tag&gt; &copy; 2026</p>
      <p>
        Multiline text: console.log("example") as any TODO
        apiKey = "abcdefghijklmnop"
      </p>
      <ul>{items.map((i) => <li key={i}>{i /* real one */}</li>)}</ul>
      {/* real two */}
    </>
  );
}
"""


def test_jsx_text_of_every_shape_is_text_to_the_parser():
    if not source_module.get_parser:
        assert reading(JSX_TEXT, ".tsx") == "abstained"
        return
    assert found(JSX_TEXT, ".tsx") == [(12, "/* real one */"), (13, "/* real two */")]
    assert rules_for("jsx-text.tsx", JSX_TEXT) == set()


def test_a_hashbang_is_neither_comment_nor_regex():
    text = "#!/usr/bin/env node\nconst a = 1; // real\n"
    for ext in (".ts", ".js", ".mjs"):
        assert found(text, ext) == scanned(text, ext) == [(2, "// real")], ext
        path = ROOT / f"bang{ext}"
        path.write_text(text)
        assert cc.load_source(path).executable_lines[0] == "#!/usr/bin/env node", ext


def test_typescript_and_jsx_free_javascript_are_still_read_without_a_parser():
    text = "export function useCount(n) {\n  return n < 10 ? n : 10; // capped\n}\nconst x = f(a) << 2;\n"
    for ext in (".js", ".mjs", ".cjs", ".ts"):
        assert scanned(text, ext) == [(2, "// capped")], ext
    assert scanned("const x = f<T>(a) << 2; // generic\n", ".ts") == [(1, "// generic")]


GRAMMAR_GAP_TS = [
    "const rows = await sql<{ n: number }[]>`select 1 // not`; // real\n",
    "const real = await importOriginal<typeof import('x')>(); // real\n",
    "let entries: import('fs').Dirent[] = []; // real\n",
    "abstract class R extends P {\n  public abstract override errorType: string; // real\n}\n",
]


def test_valid_typescript_the_grammar_rejects_is_read_exactly_by_the_scanner():
    for i, text in enumerate(GRAMMAR_GAP_TS):
        path = ROOT / f"gap{i}.ts"
        path.write_text(text)
        src = cc.load_source(path)
        assert [(c.line, c.text) for c in src.comments] == [(text.count("\n") - (1 if "abstract" in text else 0), "// real")], text
        assert src.model == "lexical", text


def test_code_rules_do_not_read_strings_templates_or_jsx_text():
    sql = 'SQL = """\nprint("this is SQL text")\nconsole.log("text")\nTODO:\napiKey = "abcdefghijklmnop"\n"""\n'
    assert rules_for("sql-text.py", sql) == set()
    docs = 'const docs = `\nconsole.log("example")\nas any\napiKey = "abcdefghijklmnop"\n`;\n'
    assert rules_for("docs-text.ts", docs) == set()
    assert rules_for("as-any-text.ts", 'const example = "as any";\n') == set()
    assert rules_for("type-text.ts", 'type Id = `as any-${string}`;\n') == set()
    assert rules_for("more-text.ts", 'const a = "hi! there";\nconst b = "it.skip(\'x\')";\nconst c = "try { a(); } catch (e) {}";\nconst r = /as any/;\n') == set()
    if source_module.get_parser:
        assert rules_for("jsx-text2.tsx", 'export const P = () => <p>console.log("example") as any TODO</p>;\n') == set()


def test_the_same_constructs_in_code_are_still_found():
    ts = 'console.log("real");\nconst value = input as any;\nconst apiKey = "abcdefghijklmnop";\nconst s = `${value as any}`;\n'
    path = ROOT / "real-code.ts"
    path.write_text(ts)
    found_rules = {v.rule: v.hits for v in cc.check_file(path, CFG, ROOT)}
    assert set(found_rules) == {"debug-output", "weak-type", "hardcoded-secret"}, found_rules
    assert [cc.line_of(h) for h in found_rules["weak-type"]] == [2, 4]
    py = 'print("real")\napi_key = "abcdefghijklmnop"\ntry:\n    run()\nexcept ValueError:\n    pass\n'
    assert rules_for("real-code.py", py) == {"debug-output", "hardcoded-secret", "swallowed-error"}


def test_a_secret_needs_a_name_in_code_and_a_whole_literal():
    assert "hardcoded-secret" not in rules_for("sec1.ts", '// const apiKey = "abcdefghijklmnop";\nconst a = 1;\n')
    assert "hardcoded-secret" in rules_for("sec2.ts", 'const apiKey = "abcdefghijklmnop"; // rotated weekly\n')
    assert "hardcoded-secret" in rules_for("sec6.ts", 'const apiKey: string = "abcdefghijklmnop";\n')
    assert "hardcoded-secret" not in rules_for("sec3.ts", 'const docs = `apiKey = "abcdefghijklmnop"`;\n')
    assert "hardcoded-secret" not in rules_for("sec4.py", 'NOTE = "api_key = \'abcdefghijklmnop\'"\n')
    assert "hardcoded-secret" in rules_for("sec5.py", 'api_key = "abcdefghijklmnop"  # from the vendor portal\n')


JSX_CONTEXTS = [
    "export function f() {\n  throw <p>Run ```bash to start</p>;\n}\n// TODO: real defect\n",
    "export const v = void <Component />;\n// TODO: real defect\n",
    "export const d = delete <Component />;\n// TODO: real defect\n",
    "export const t = typeof <Component />;\n// TODO: real defect\n",
    "export const F = () => <>fragment https://example.com/TODO</>;\n// TODO: real defect\n",
    "export function Card() {\n  return <div className=\"card\">it's here</div>;\n}\n// TODO: real defect\n",
]


def test_tsx_and_jsx_are_read_only_by_a_parser_that_accepts_them():
    for text in JSX_CONTEXTS + [SOL_FALSE_NEGATIVE]:
        for ext in (".tsx", ".jsx"):
            if source_module.get_parser:
                assert found(text, ext)[-1][1] == "// TODO: real defect", (ext, text)
            else:
                assert reading(text, ext) == "abstained", (ext, text)
    assert reading(SOL_FALSE_COMMENT, ".tsx") == ([] if source_module.get_parser else "abstained")
    assert reading("export const n = 1; // no JSX at all\n", ".tsx") == (
        [(1, "// no JSX at all")] if source_module.get_parser else "abstained")


def test_a_tsx_edit_without_a_parser_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    path = box / "throw.tsx"
    text = JSX_CONTEXTS[0]
    path.write_text(text)
    kind, said = outcome(post(claude_payload(path, [(1, ["+" + l for l in text.split("\n")])], "tsx-throw"), box))
    if source_module.get_parser:
        assert kind == "report" and "TODO/FIXME: do it or remove: L4" in said, said
    else:
        assert kind == "context" and "throw.tsx not checked" in said and "tree-sitter" in said, said


def test_the_js_scanner_abstains_unless_a_less_than_follows_an_operand():
    for text in JSX_CONTEXTS + ["const x = cond ? < div /> : null;\n", "return (\n  <div/>\n);\n"]:
        for ext in (".js", ".mjs", ".cjs"):
            try:
                source_module.scan_comments(text, ext)
            except cc.Unparsable as exc:
                assert "JSX" in str(exc)
            else:
                raise AssertionError(f"the scanner read possible JSX in {ext}: {text!r}")
    operators = ("for (let i = 0; i<n; i++) {} // a\nif (a < b) x = y << 2 <= z; // b\n"
                 "const s = 'x' < t, r = f(1) < g[2]; // c\nconst n = this < that; // d\n")
    assert scanned(operators, ".js") == [(1, "// a"), (2, "// b"), (3, "// c"), (4, "// d")]


def test_debug_calls_are_found_anywhere_in_executable_code():
    real = ('console.log("real");\nconst x = ready && console.log("real");\nconst m = `result: ${console.log("real")}`;\n'
            'run(console.debug("real"));\n')
    path = ROOT / "debug-real.ts"
    path.write_text(real)
    [debug] = [v for v in cc.check_file(path, CFG, ROOT) if v.rule == "debug-output"]
    assert [cc.line_of(h) for h in debug.hits] == [1, 2, 3, 4]
    fake = ("const a = \"console.log('example')\";\nconst b = `console.log(\"example\")`;\nconst r = /console.log\\(/;\n"
            "logger.print(x);\n")
    assert "debug-output" not in rules_for("debug-fake.ts", fake)
    assert "debug-output" not in rules_for("debug-fake.py", "def print(self):\n    return 1\nblueprint(x)\n")
    if source_module.get_parser:
        assert "debug-output" in rules_for("debug-jsx.tsx", 'export const P = () => <p>{console.log("real")}</p>;\n')
        assert "debug-output" not in rules_for("debug-jsx-text.tsx", 'export const P = () => <p>console.log("example")</p>;\n')


def test_f_string_fields_are_code_and_their_text_is_data():
    assert "skipped-test" in rules_for("fs1.py", "value = f\"{it.skip('x')}\"\n")
    assert "hardcoded-secret" in rules_for("fs2.py", "value = f\"{foo(api_key='abcdefghijklmnop')}\"\n")
    assert "debug-output" in rules_for("fs3.py", "value = f\"prefix {print('real')} suffix\"\n")
    text = ("a = f\"print('x') {name} it.skip('y')\"\nb = f\"{{print('x')}} {value:>{width}}\"\n"
            "c = rf\"\\d{n} print(\"\nd = f\"{name!r:>10} api_key = 'abcdefghijklmnop'\"\n"
            "e = (f\"one {x} print('z')\"\n     f\"two {y}\")\nf = f\"{ {'k': 'print(1)'}['k'] }\"\n")
    assert rules_for("fs-text.py", text) == set()


def test_an_f_string_field_older_pythons_cannot_locate_is_not_checked():
    nested = "value = f\"{', '.join(f'{v}' for v in values)}\"\n"
    multiline = 'value = f"""\n{print("real")}\n"""\n'
    if sys.version_info >= (3, 12):
        assert rules_for("fs-nested.py", nested) == set()
        assert "debug-output" in rules_for("fs-multi.py", multiline)
        return
    for name, text in (("fs-nested.py", nested), ("fs-multi.py", multiline)):
        try:
            rules_for(name, text)
        except cc.Unparsable as exc:
            assert "f-string" in str(exc), exc
        else:
            raise AssertionError(f"{name} was read although its f-string field cannot be located")


def test_a_hook_object_that_names_no_edit_reaches_the_agent_as_not_checked():
    box = Path(tempfile.mkdtemp())
    for payload in ({}, {"tool_name": "Edit", "tool_input": {}}, {"tool_name": "Edit", "tool_input": "x"},
                    {"tool_name": "apply_patch", "tool_input": {"command": "not a patch"}},
                    {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** End Patch"}}):
        proc = post(payload, box)
        assert (proc.returncode, proc.stderr) == (0, ""), proc
        kind, said = outcome(proc)
        assert kind == "context" and said == "clean-code: not checked, the hook input names no edit to check", said


def test_a_valid_edit_that_introduces_nothing_is_silent():
    box = Path(tempfile.mkdtemp())
    path = box / "quiet.ts"
    path.write_text(f"{LEGACY}\n\nconst b = 2;\n")
    deletion = claude_payload(path, [(1, [f" {LEGACY}", "-const a = 1;", " ", " const b = 2;"])], "quiet-delete")
    blank = claude_payload(path, [(1, [f" {LEGACY}", "+", " const b = 2;"])], "quiet-blank")
    for payload in (deletion, blank):
        proc = post(payload, box)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), proc


BOUNDARY = "client module reaches a server-only package"


def edit(box: Path, relative: str, text: str, edited: list, session: str) -> tuple:
    """Write a file as an edit left it and post the hook payload claiming exactly the edited lines."""
    path = box / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    lines = text.split("\n")
    return outcome(post(claude_payload(path, [(n, ["+" + lines[n - 1]]) for n in edited], session), box))


def test_an_edited_server_only_import_in_a_client_is_reported_on_its_line():
    box = next_like_project()
    text = '"use client";\nimport Redis from "ioredis";\nexport const P = () => Redis;\n'
    kind, said = edit(box, "components/direct.ts", text, [2], "bnd-direct")
    assert kind == "report" and f"{BOUNDARY}: L2" in said, said
    kind, said = edit(box, "components/legacy.ts", text + "export const Q = 1;\n", [4], "bnd-legacy")
    assert kind == "silent", said


def test_adding_use_client_makes_every_existing_import_accountable():
    box = next_like_project()
    kind, said = edit(box, "components/turned.ts", '"use client";\nimport Redis from "ioredis";\nexport const P = () => Redis;\n',
                      [1], "bnd-directive")
    assert kind == "report" and f"{BOUNDARY}: L1" in said, said


def test_type_only_imports_are_not_runtime_edges():
    box = next_like_project()
    for i, line in enumerate(['import type { Redis } from "ioredis";', 'import { type Redis } from "ioredis";',
                              'import { type Redis, type Cluster } from "ioredis";', 'export type { Redis } from "ioredis";']):
        kind, said = edit(box, f"components/types{i}.ts", f'"use client";\n{line}\nexport const P = 1;\n', [2], f"bnd-type{i}")
        assert kind == "silent", (line, said)
    kind, said = edit(box, "components/mixed.ts", '"use client";\nimport { type Redis, Cluster } from "ioredis";\nexport const P = Cluster;\n',
                      [2], "bnd-mixed")
    assert kind == "report" and f"{BOUNDARY}: L2" in said, said


def test_runtime_edges_of_every_form_are_followed():
    box = next_like_project()
    forms = ['import "ioredis";', 'export { default } from "ioredis";', 'export * from "../lib/redis";',
             'const m = await import("ioredis");', 'const r = require("ioredis");',
             'import {\n  TTL_MS,\n} from "../lib/presence";']
    for i, form in enumerate(forms):
        text = f'"use client";\n{form}\nexport const P = 1;\n'
        edited = [3] if "TTL_MS" in form else [2]
        kind, said = edit(box, f"components/form{i}.ts", text, edited, f"bnd-form{i}")
        assert kind == "report" and BOUNDARY in said, (form, said)


def test_a_path_of_any_depth_is_found_and_a_cycle_ends():
    box = next_like_project()
    (box / "lib/a.ts").write_text('import { b } from "./b";\nexport const a = b;\n')
    (box / "lib/b.ts").write_text('import { c } from "./c";\nimport { a } from "./a";\nexport const b = c;\n')
    (box / "lib/c.ts").write_text('import { d } from "./d/index";\nexport const c = d;\n')
    (box / "lib/d").mkdir()
    (box / "lib/d/index.ts").write_text('import { TTL_MS } from "../presence";\nexport const d = TTL_MS;\n')
    kind, said = edit(box, "components/deep.ts", '"use client";\nimport { a } from "../lib/a";\nexport const P = a;\n', [2], "bnd-deep")
    assert kind == "report" and f"{BOUNDARY}: L2" in said, said
    (box / "lib/loop1.ts").write_text('import { two } from "./loop2";\nexport const one = two;\n')
    (box / "lib/loop2.ts").write_text('import { one } from "./loop1";\nexport const two = one;\n')
    kind, said = edit(box, "components/loop.ts", '"use client";\nimport { one } from "../lib/loop1";\nexport const P = one;\n', [2], "bnd-loop")
    assert kind == "silent", said


def test_an_edit_below_a_client_is_reported_where_the_new_edge_is():
    box = next_like_project()
    (box / "lib/format.ts").write_text("export const format = (n: number) => String(n);\n")
    (box / "components/view.ts").write_text('"use client";\nimport { format } from "../lib/format";\nexport const V = format;\n')
    text = 'import { redis } from "./redis";\nexport const format = (n: number) => String(n) + redis;\n'
    kind, said = edit(box, "lib/format.ts", text, [1], "bnd-below")
    assert kind == "report" and f"{BOUNDARY}: L1" in said, said
    kind, said = edit(box, "lib/unused.ts", text, [1], "bnd-unreached")
    assert kind == "silent", said


def test_a_server_action_ends_the_path():
    box = next_like_project()
    kind, said = edit(box, "components/action.ts", '"use client";\nimport { read } from "../actions/read";\nexport const P = read;\n',
                      [2], "bnd-action")
    assert kind == "silent", said


def test_only_builtins_nextjs_cannot_polyfill_are_server_only():
    box = next_like_project()
    for i, (spec, bad) in enumerate([("fs", True), ("node:fs", True), ("fs/promises", True), ("child_process", True),
                                     ("server-only", True), ("next/headers", True), ("path", False), ("crypto", False),
                                     ("http", False), ("node:path", False), ("react", False)]):
        kind, said = edit(box, f"components/builtin{i}.ts", f'"use client";\nimport "{spec}";\nexport const P = 1;\n', [2], f"bnd-b{i}")
        assert (kind == "report") == bad, (spec, said)


def test_an_installed_package_with_a_browser_build_is_taken_at_its_word():
    box = next_like_project()
    (box / "node_modules/pg").mkdir(parents=True)
    (box / "node_modules/pg/package.json").write_text('{"name": "pg", "browser": "./lib/browser.js"}')
    kind, said = edit(box, "components/pg.ts", '"use client";\nimport pg from "pg";\nexport const P = pg;\n', [2], "bnd-pg")
    assert kind == "silent", said
    # Turning off one native submodule is not a browser build of the package.
    (box / "node_modules/pg/package.json").write_text('{"name": "pg", "browser": {"./lib/native": false}}')
    kind, said = edit(box, "components/pg2.ts", '"use client";\nimport pg from "pg";\nexport const P = pg;\n', [2], "bnd-pg2")
    assert kind == "report", said


def test_aliases_come_from_the_project_config():
    box = next_like_project()
    (box / "src/lib").mkdir(parents=True)
    (box / "src/lib/db.ts").write_text('import { Pool } from "pg";\nexport const db = new Pool();\n')
    (box / "tsconfig.json").write_text('{\n  // comments and trailing commas are allowed here\n  "compilerOptions": {\n'
                                       '    "paths": { "@/*": ["./src/*"], },\n  },\n}\n')
    kind, said = edit(box, "src/app/page.ts", '"use client";\nimport { db } from "@/lib/db";\nexport const P = db;\n', [2], "bnd-alias")
    assert kind == "report" and f"{BOUNDARY}: L2" in said, said


def test_an_edge_that_cannot_be_resolved_is_not_checked_rather_than_clean():
    box = next_like_project()
    for i, line in enumerate(['import { db } from "@/lib/db";', 'import { x } from "./missing";', 'const m = await import(name);']):
        kind, said = edit(box, f"components/unknown{i}.ts", f'"use client";\n{line}\nexport const P = 1;\n', [2], f"bnd-unk{i}")
        assert kind == "context" and "client-bundles-server-code not checked" in said, (line, said)


def test_a_graph_larger_than_the_budget_is_not_checked_rather_than_clean():
    from clean_code import boundary
    box = next_like_project()
    for i in range(6):
        (box / f"lib/chain{i}.ts").write_text(f'import {{ v }} from "./chain{i + 1}";\nexport const v = 1;\n')
    (box / "lib/chain6.ts").write_text("export const v = 1;\n")
    path = box / "components/chain.ts"
    path.write_text('"use client";\nimport { v } from "../lib/chain0";\nexport const P = v;\n')
    budget = boundary.MAX_MODULES
    boundary.MAX_MODULES = 3
    try:
        [found] = [v for v in cc.check_file(path, CFG, box, {2}) if v.rule == "client-bundles-server-code"]
    finally:
        boundary.MAX_MODULES = budget
    assert found.incomplete and not found.hits, found


def test_the_grammars_the_installer_provides_are_used():
    try:
        import tree_sitter_typescript  # noqa: F401
    except ImportError:
        return
    assert source_module.get_parser is not None
    assert source_module.parsed_reading("export const A = () => <p>it's</p>; // c\n", "tsx").comments == [(36, 40, "// c")]


def test_base_url_makes_a_bare_specifier_a_local_module():
    box = next_like_project()
    (box / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": "src"}}')
    (box / "src/server").mkdir(parents=True)
    (box / "src/store").mkdir()
    (box / "src/lib.ts").write_text('import Redis from "ioredis";\nexport const thing = new Redis();\n')
    (box / "src/server/db.ts").write_text('import { Pool } from "pg";\nexport const db = new Pool();\n')
    (box / "src/store/index.ts").write_text('import Redis from "ioredis";\nexport const store = new Redis();\n')
    (box / "src/react.ts").write_text('import Redis from "ioredis";\nexport default Redis;\n')
    for i, spec in enumerate(["lib", "server/db", "store", "react"]):
        kind, said = edit(box, f"src/client{i}.ts", f'"use client";\nimport thing from "{spec}";\nexport const P = thing;\n',
                          [2], f"base-url-{i}")
        assert kind == "report" and f"{BOUNDARY}: L2" in said, (spec, said)
    kind, said = edit(box, "src/client9.ts", '"use client";\nimport { useState } from "zustand";\nexport const P = useState;\n',
                      [2], "base-url-package")
    assert kind == "silent", said


def test_an_edited_index_module_is_protected_however_a_client_names_it():
    for i, spec in enumerate(["../lib/dir", "../lib/dir/index", "../lib/dir/index.ts", "@/lib/dir"]):
        box = next_like_project()
        (box / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"@/*": ["./*"]}}}')
        (box / "lib/dir").mkdir()
        (box / "lib/dir/index.ts").write_text("export const v = 1;\n")
        (box / "components/view.ts").write_text(f'"use client";\nimport {{ v }} from "{spec}";\nexport const V = v;\n')
        text = 'import Redis from "ioredis";\nexport const v = new Redis();\n'
        kind, said = edit(box, "lib/dir/index.ts", text, [1], f"reverse-index-{i}")
        assert kind == "report" and f"{BOUNDARY}: L1" in said, (spec, said)
    box = next_like_project()
    (box / "lib/dir").mkdir()
    kind, said = edit(box, "lib/dir/index.ts", 'import Redis from "ioredis";\nexport const v = 1;\n', [1], "reverse-index-none")
    assert kind == "silent", said


BROWSER_FIELDS = [
    ('{"name": "ioredis", "browser": false}', True),
    ('{"name": "ioredis"}', True),
    ('{"name": "ioredis", "description": "not for the browser", "keywords": ["browser"]}', True),
    ('{"name": "ioredis", "main": "index.js", "browser": {"./index.js": false}}', True),
    ('{"name": "ioredis", "main": "index.js", "browser": {"./lib/native.js": false}}', True),
    ('{"name": "ioredis", "browser": "dist/browser.js"}', False),
    ('{"name": "ioredis", "main": "./built/index.js", "browser": {"./built/index.js": "./built/browser.js"}}', False),
    ('{"name": "ioredis", "exports": {".": {"browser": "./browser.js", "default": "./index.js"}}}', False),
]


def test_only_a_usable_browser_entry_exempts_a_server_only_package():
    for i, (manifest, reported) in enumerate(BROWSER_FIELDS):
        box = next_like_project()
        (box / "node_modules/ioredis").mkdir(parents=True)
        (box / "node_modules/ioredis/package.json").write_text(manifest)
        kind, said = edit(box, "components/r.ts", '"use client";\nimport Redis from "ioredis";\nexport const P = Redis;\n',
                          [2], f"browser-{i}")
        assert (kind == "report") == reported, (manifest, said)


def test_a_hook_that_only_mentions_the_checker_is_not_ours():
    home = fresh_home()
    check = home / ".clean-code/clean_check.py"
    settings = {"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": f'python3 "{check}" stop'}]},
                 {"hooks": [{"type": "command", "command": f"echo documentation: {check}"}]}],
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": f"cat {check} | wc -l"}]}]}}
    (home / ".claude/settings.json").write_text(json.dumps(settings))
    assert run_installer(home, "--claude", "--no-parser")[0] == 0
    after = json.loads((home / ".claude/settings.json").read_text())["hooks"]
    assert [g["hooks"][0]["command"] for g in after["Stop"]] == [f"echo documentation: {check}"]
    assert f"cat {check} | wc -l" in [g["hooks"][0]["command"] for g in after["PostToolUse"]]
    assert len(ours(home, home / ".claude/settings.json", "PostToolUse")) == 1


def test_a_hook_shape_the_installer_does_not_understand_stops_it_before_any_change():
    for hooks in ({"PostToolUse": {"matcher": "Edit", "hooks": [{"type": "command", "command": "theirs"}]}},
                  {"PostToolUse": "theirs"},
                  {"PreToolUse": [{"matcher": "Bash", "hooks": []}], "Stop": 7},
                  "not an object"):
        home = fresh_home()
        assert run_installer(home, "--codex", "--no-parser")[0] == 0
        settings = home / ".claude/settings.json"
        settings.write_text(json.dumps({"theme": "dark", "hooks": hooks}))
        before = snapshot(home)
        code, said = run_installer(home, "--no-parser")
        assert code == 1 and "Nothing was changed" in said, (hooks, said)
        assert snapshot(home) == before, hooks
        settings.write_text(json.dumps({"theme": "dark"}))
        assert run_installer(home, "--no-parser")[0] == 0


def test_no_parser_means_no_parser_whatever_the_interpreter_has():
    home = fresh_home("no parser")
    code, said = run_installer(home, "--claude", "--no-parser")
    assert code == 0 and "React, TSX and JSX checking: OFF" in said, said
    [group] = ours(home, home / ".claude/settings.json", "PostToolUse")
    page = home / "app/page.tsx"
    page.parent.mkdir()
    text = "export default function Page() {\n  return <p>{console.log(1)}</p>;\n}\n"
    page.write_text(text)
    payload = json.dumps(claude_payload(page, [(2, ["+" + text.split("\n")[1]])], "no-parser"))
    proc = subprocess.run(group["hooks"][0]["command"], shell=True, input=payload, capture_output=True, text=True,
                          cwd=str(page.parent))
    kind, said = outcome(proc)
    assert kind == "context" and "page.tsx not checked" in said, (proc, source_module.get_parser)


def test_a_boolean_that_short_circuits_is_a_branch():
    if not __import__("clean_code.ast_rules", fromlist=["AVAILABLE"]).AVAILABLE:
        return
    assert "flag-argument" in ast_rules_for("short.ts", "export function render(compact: boolean) {\n  return compact && one();\n}\n")
    assert "flag-argument" in ast_rules_for("short.py", "def render(compact: bool):\n    return compact and one()\n")
    assert "flag-argument" not in ast_rules_for("both.ts", "export function both(a: boolean, b: boolean) {\n  return save(a && b);\n}\n")


WEAK = cc.BY_ID["weak-type"].label
DEBUG = cc.BY_ID["debug-output"].label
SHELL_STATE = Path(tempfile.mkdtemp())
CALLS = iter(range(10 ** 6))


def git_in(box: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", "-C", str(box),
                    *args], check=True, capture_output=True)


def repo(files: dict, box: "Path | None" = None) -> Path:
    """A Git worktree with these files committed, for a shell command to change."""
    box = box or Path(tempfile.mkdtemp()) / "repo"
    for name, text in files.items():
        (box / name).parent.mkdir(parents=True, exist_ok=True)
        (box / name).write_text(text)
    git_in(box, "init", "-q")
    git_in(box, "add", "-A")
    git_in(box, "commit", "-q", "-m", "start")
    return box


def hook(mode: str, payload: dict, box: Path, state: Path = SHELL_STATE) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ENTRY), mode], input=json.dumps(payload), capture_output=True, text=True,
                          env={"CLAUDE_PROJECT_DIR": str(box), "PATH": os.environ["PATH"], "TMPDIR": str(state)},
                          cwd=str(box))


def shell_payload(box: Path, command: str, **extra) -> dict:
    call = next(CALLS)
    inp = {"command": command, **extra.pop("tool_input", {})}
    return {"session_id": f"shell-{call}", "tool_use_id": f"toolu_{call}", "tool_name": "Bash", "cwd": str(box),
            "hook_event_name": "PreToolUse", "tool_input": inp, **extra}


def finish(payload: dict, box: Path, code: int = 0, state: Path = SHELL_STATE) -> tuple:
    """The post hook for a command that already ran, on the event each outcome fires."""
    event = "PostToolUseFailure" if code and "turn_id" not in payload else "PostToolUse"
    return outcome(hook("post", {**payload, "hook_event_name": event, "tool_response": {"exit_code": code}}, box, state))


def shell(box: Path, command: str, **extra) -> tuple:
    """Pre hook, the real command in the worktree, post hook: what the agent reads afterwards."""
    payload = shell_payload(box, command, **extra)
    assert hook("pre", payload, box).returncode == 0
    ran = subprocess.run(command, shell=True, cwd=str(box), capture_output=True)
    return finish(payload, box, ran.returncode)


PY = json.dumps(sys.executable)
# Only a birth time kept with the inode proves a file was moved; Linux keeps none.
BIRTHTIME = hasattr(os.lstat(__file__), "st_birthtime")


def test_the_structured_pre_gaps_are_closed():
    box = Path(tempfile.mkdtemp())
    deletion = {"tool_name": "apply_patch", "cwd": str(box),
                "tool_input": {"command": "*** Begin Patch\n*** Delete File: cart.test.ts\n*** End Patch"}}
    assert "deletes the test file cart.test.ts" in cc.pre_check(deletion, box)
    for name in ("test_cart.py", "cart_test.go", "__tests__/cart.ts", "cart.spec.tsx"):
        deletion["tool_input"]["command"] = f"*** Begin Patch\n*** Delete File: {name}\n*** End Patch"
        assert cc.pre_check(deletion, box), name
    deletion["tool_input"]["command"] = "*** Begin Patch\n*** Delete File: cart.ts\n*** End Patch"
    assert cc.pre_check(deletion, box) is None
    typed = box / "typed.ts"
    typed.write_text("export function load(id: string, strict: boolean): Row {\n  return rows[id];\n}\n")
    write = {"tool_name": "Write", "tool_input": {"file_path": str(typed),
             "content": "export function load(id, strict) {\n  return rows[id];\n}\n"}}
    assert "removes type annotations" in cc.pre_check(write, box)
    write["tool_input"]["content"] = typed.read_text().replace("Row {", "Row | undefined {")
    assert cc.pre_check(write, box) is None


def test_structured_edits_keep_their_exact_scope_beside_shell_coverage():
    box = repo({"a.ts": "export const a = 1;\n"})
    (box / "a.ts").write_text("export const a = 1;\nexport const b = 2;\n")
    assert outcome(post(claude_payload(box / "a.ts", [(2, ["+export const b = 2;"])], "st-edit"), box)) == ("silent", "")
    (box / "w.ts").write_text("export const w = 1;\n")
    created = {"session_id": "st-write", "tool_name": "Write", "tool_input": {"file_path": str(box / "w.ts")},
               "tool_response": {"type": "create", "content": "export const w = 1;\n"}}
    assert outcome(post(created, box)) == ("silent", "")
    (box / "p.ts").write_text("console.log(1);\n")
    patch = {"tool_name": "apply_patch", "cwd": str(box), "session_id": "st-patch",
             "tool_input": {"command": "*** Begin Patch\n*** Add File: p.ts\n+console.log(1);\n*** End Patch"}}
    kind, said = outcome(post(patch, box))
    assert kind == "report" and f"{DEBUG}: L1" in said, said


def test_every_way_a_shell_can_write_is_checked_after_it_runs():
    box = repo({"a.ts": "export const a: number = 1;\n", "b.ts": "export const b = 2;\n"})
    kind, said = shell(box, "sed -i.orig 's/: number = 1/ = b as any/' a.ts && rm a.ts.orig")
    assert kind == "report" and "a.ts" in said and f"{WEAK}: L1" in said, said
    script = "import pathlib; p = pathlib.Path('b.ts'); p.write_text(p.read_text() + 'console.log(1);\\n')"
    kind, said = shell(box, f"{PY} -c \"{script}\"")
    assert kind == "report" and f"{DEBUG}: L2" in said, said
    kind, said = shell(box, "cat > fresh.ts <<'EOF'\nexport const fresh: number = 3;\nEOF")
    assert kind == "silent", said
    assert shell(box, "cp fresh.ts copy.ts") == ("silent", "")
    import shutil as _shutil
    if _shutil.which("node"):
        kind, said = shell(box, "node -e \"require('fs').appendFileSync('fresh.ts', 'console.log(2);\\n')\"")
        assert kind == "report" and "fresh.ts" in said and f"{DEBUG}: L2" in said, said


def test_a_rename_adds_nothing_and_a_rename_with_an_edit_adds_only_the_edit():
    box = repo({"legacy.ts": "console.log('legacy');\nexport const a = 1;\n", "old.ts": "console.log('old');\nexport const o = 1;\n"})
    assert shell(box, "mv legacy.ts moved.ts") == ("silent", "")
    kind, said = shell(box, "mv old.ts renamed.ts && printf 'console.log(1);\\n' >> renamed.ts")
    if BIRTHTIME:
        assert kind == "report" and f"{DEBUG}: L3" in said and "L1" not in said, said
    else:
        assert kind == "context" and "renamed.ts not checked" in said, "an inode alone proves no move"


def test_a_multi_file_command_reports_only_the_file_that_has_the_defect():
    box = repo({"keep.ts": "export const k = 1;\n"})
    kind, said = shell(box, "printf 'export const one = 1;\\n' > one.ts && printf 'console.log(3);\\n' > two.ts")
    assert kind == "report" and "two.ts" in said and "one.ts" not in said, said


def test_a_command_that_fails_after_writing_is_still_checked():
    box = repo({"keep.ts": "export const k = 1;\n"})
    kind, said = shell(box, "printf 'console.log(4);\\n' > failed.ts; exit 7")
    assert kind == "report" and "failed.ts" in said and f"{DEBUG}: L1" in said, said


def test_work_already_in_the_worktree_is_never_charged_to_the_command():
    box = repo({"dirty.ts": "export const d = 1;\n", "staged.ts": "export const s = 1;\n", "both.ts": "export const x = 1;\n"})
    (box / "dirty.ts").write_text("export const d = 1;\nconsole.log('legacy');\n")
    kind, said = shell(box, "printf \"console.log('new');\\n\" >> dirty.ts")
    assert kind == "report" and f"{DEBUG}: L3" in said and "L2" not in said, said
    (box / "staged.ts").write_text("console.log('staged');\n")
    git_in(box, "add", "staged.ts")
    assert shell(box, "ls && git status --short") == ("silent", "")
    (box / "both.ts").write_text("console.log('staged');\n")
    git_in(box, "add", "both.ts")
    (box / "both.ts").write_text("console.log('staged');\nconsole.log('worktree');\n")
    assert shell(box, "printf 'export const y = 2;\\n' >> both.ts") == ("silent", "")
    (box / "untracked.ts").write_text("console.log('untracked');\n")
    assert shell(box, "printf 'export const u = 3;\\n' >> untracked.ts") == ("silent", "")
    kind, said = shell(box, "printf 'export const w = v as any;\\n' > weak.ts")
    assert kind == "report" and f"{WEAK}: L1" in said, said
    assert shell(box, "rm dirty.ts untracked.ts") == ("silent", "")


def test_a_commit_inside_the_command_does_not_hide_what_it_wrote():
    box = repo({"a.ts": "export const a = 1;\n"})
    kind, said = shell(box, "printf 'console.log(1);\\n' >> a.ts && git -c user.name=t -c user.email=t@t commit -qam next")
    assert kind == "report" and f"{DEBUG}: L2" in said, said


def test_a_shell_edit_below_a_client_is_reported_at_the_new_edge():
    box = next_like_project()
    (box / "lib/format.ts").write_text("export const format = (n: number) => String(n);\n")
    (box / "components/view.ts").write_text('"use client";\nimport { format } from "../lib/format";\nexport const V = format;\n')
    repo({}, box)
    script = ("import pathlib; p = pathlib.Path('lib/format.ts'); "
              "p.write_text('import { redis } from \\\"./redis\\\";\\n' + p.read_text())")
    kind, said = shell(box, f"{PY} -c \"{script}\"")
    assert kind == "report" and "format.ts" in said and f"{BOUNDARY}: L1" in said, said


def test_a_shell_command_that_weakens_a_safeguard_is_told_to_undo_it():
    box = repo({"cart.test.ts": "test('x', () => {});\n", "typed.ts": "export function f(a: number, b: string): void {}\n",
                "tsconfig.json": "{}\n"})
    kind, said = shell(box, f"{PY} -c \"import os; os.remove('cart.test.ts')\"")
    assert kind == "report" and "deletes the test file cart.test.ts" in said and "undo" in said, said
    kind, said = shell(box, "sed -i.orig 's/(a: number, b: string): void/(a, b)/' typed.ts && rm typed.ts.orig")
    assert kind == "report" and "removes type annotations" in said, said
    loosened = json.dumps(json.dumps({"compilerOptions": {"strict": False}}))
    kind, said = shell(box, f"printf '%s\\n' {loosened} > tsconfig.json")
    assert kind == "report" and "strict mode" in said, said


def test_a_read_only_command_says_nothing():
    box = repo({"a.ts": "console.log('legacy');\n"})
    assert shell(box, "ls -la && cat a.ts && git log --oneline") == ("silent", "")


def test_writers_running_at_once_in_one_worktree_are_not_checked():
    box = repo({"a.ts": "export const a = 1;\n"})
    first, second = shell_payload(box, "one"), shell_payload(box, "two")
    assert hook("pre", first, box).returncode == 0 and hook("pre", second, box).returncode == 0
    (box / "one.ts").write_text("console.log(1);\n")
    (box / "two.ts").write_text("export const t = 2;\n")
    for payload in (first, second):
        kind, said = finish(payload, box)
        assert kind == "context" and "at the same time" in said, said
    reader = shell_payload(box, "ls")
    other = shell_payload(box, "git status")
    assert hook("pre", reader, box).returncode == 0 and hook("pre", other, box).returncode == 0
    assert finish(reader, box) == ("silent", "") and finish(other, box) == ("silent", "")


def test_a_post_without_its_snapshot_is_not_checked():
    box = repo({"a.ts": "export const a = 1;\n"})
    (box / "late.ts").write_text("console.log(1);\n")
    kind, said = finish(shell_payload(box, "printf x"), box)
    assert kind == "context" and "no snapshot" in said, said
    payload = shell_payload(box, "printf x")
    del payload["tool_use_id"]
    kind, said = finish(payload, box)
    assert kind == "context" and "not checked" in said, said


README = (CORE.parent / "README.md").read_text(encoding="utf-8")
SKILL = (CORE.parent / "skills/clean-code/SKILL.md").read_text(encoding="utf-8")


def test_writes_after_the_post_hook_are_outside_what_is_observed_and_the_docs_say_so():
    box = repo({"a.ts": "export const a = 1;\n"})
    detached = "sh -c 'nohup sh -c \"sleep 1; printf \\\"console.log(5);\\\\n\\\" > late.ts\" >/dev/null 2>&1 &'"
    assert shell(box, detached) == ("silent", ""), "nothing was written by the time the command returned"
    for _ in range(50):
        if (box / "late.ts").exists():
            break
        time.sleep(0.1)
    assert "console.log(5)" in (box / "late.ts").read_text(), "the descendant wrote after the post hook"
    for doc in (README, SKILL):
        assert "after the tool completion event" in doc and "outside that observation boundary" in doc
        assert "bounded foreground" not in doc
    kind, said = shell(box, "printf 'export const b = 1;\\n' > b.ts", tool_input={"run_in_background": True})
    assert kind == "context" and "background" in said, said


def test_git_ignored_files_are_outside_shell_coverage_and_the_docs_say_so():
    box = repo({".gitignore": "gen/\n", "a.ts": "export const a = 1;\n"})
    (box / "gen").mkdir()
    (box / "gen/old.ts").write_text("export const o = 1;\n")
    command = "printf 'console.log(1);\\n' >> gen/old.ts && printf 'console.log(2);\\n' > gen/new.ts"
    assert shell(box, command) == ("silent", "")
    for doc in (README, SKILL):
        assert "Git-ignored paths are outside shell coverage unless they are already tracked" in doc


def test_a_large_file_changed_behind_the_same_size_inode_and_time_is_not_checked():
    box = repo({"a.ts": "export const a = 1;\n", "tracked.ts": "export const t = 1;\n"})
    for name in ("huge.ts", "tracked.ts"):
        (box / name).write_text("export const n = 1;\n" * 150_000)
        before = os.stat(box / name)
        payload = shell_payload(box, "edit in place")
        assert hook("pre", payload, box).returncode == 0
        with open(box / name, "r+b") as handle:
            handle.write(b"console.log(1);  ")
        os.utime(box / name, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(box / name)
        assert (after.st_size, after.st_ino, after.st_mtime_ns) == (before.st_size, before.st_ino, before.st_mtime_ns)
        kind, said = finish(payload, box)
        assert kind == "context" and f"{name} not checked" in said, (name, said)


def test_a_large_file_left_alone_is_not_reported():
    box = repo({"a.ts": "export const a = 1;\n"})
    (box / "huge.ts").write_text("export const n = 1;\n" * 150_000)
    assert shell(box, "cat huge.ts > /dev/null") == ("silent", "")


def test_a_moved_file_is_diffed_against_itself_however_heavily_it_was_edited():
    body = "console.log('legacy');\n" + "".join(f"export const v{i} = {i};\n" for i in range(20))
    box = repo({"legacy.ts": body})
    rewrite = ("import pathlib; p = pathlib.Path('moved.ts'); first = p.read_text().split(chr(10))[0]; "
               "p.write_text(first + chr(10) + ''.join(f'export const w{i} = {i};' + chr(10) for i in range(20)) "
               "+ 'console.log(1);' + chr(10))")
    kind, said = shell(box, f"mv legacy.ts moved.ts && {PY} -c \"{rewrite}\"")
    if BIRTHTIME:
        assert kind == "report" and f"{DEBUG}: L22" in said and "L1," not in said and ": L1\n" not in said, said
    else:
        assert kind == "context" and "moved.ts not checked" in said, "an inode alone proves no move"


def test_a_file_moved_over_another_is_diffed_against_where_it_came_from():
    box = repo({"a.ts": "console.log('legacy');\nexport const a = 1;\n", "b.ts": "export const b = 1;\n"})
    kind, said = shell(box, "mv a.ts b.ts")
    if BIRTHTIME:
        assert (kind, said) == ("silent", ""), said
    else:
        assert kind == "context" and "b.ts not checked" in said and "moved over it" in said, said


def test_copy_then_delete_of_identical_content_is_a_rename():
    box = repo({"a.ts": "console.log('legacy');\nexport const a = 1;\n"})
    assert shell(box, "cp a.ts c.ts && rm a.ts") == ("silent", "")


def test_a_file_too_large_to_keep_is_not_checked_when_it_changes():
    box = repo({"a.ts": "export const a = 1;\n"})
    (box / "huge.ts").write_text("export const n = 1;\n" * 150_000)
    kind, said = shell(box, "printf 'console.log(1);\\n' >> huge.ts")
    assert kind == "context" and "huge.ts not checked" in said and "too large" in said, said


def test_a_link_out_of_the_worktree_is_not_followed():
    outside = Path(tempfile.mkdtemp()) / "target.ts"
    outside.write_text("export const t = 1;\n")
    box = Path(tempfile.mkdtemp()) / "repo"
    box.mkdir()
    (box / "link.ts").symlink_to(outside)
    repo({"a.ts": "export const a = 1;\n"}, box)
    kind, said = shell(box, "printf 'console.log(1);\\n' >> link.ts")
    assert kind == "context" and "link.ts not checked" in said and "link out of the worktree" in said, said


def test_a_shell_command_outside_git_is_not_checked():
    box = Path(tempfile.mkdtemp())
    kind, said = shell(box, "printf 'console.log(1);\\n' > a.ts")
    assert kind == "context" and "outside a Git worktree" in said, said


def test_nested_repositories_are_outside_the_worktree():
    box = repo({"a.ts": "export const a = 1;\n"})
    repo({"inner.ts": "export const i = 1;\n"}, box / "nested")
    kind, said = shell(box, "printf 'console.log(1);\\n' >> nested/inner.ts")
    assert kind == "context" and "nested not checked" in said, said


def test_paths_with_spaces_and_unicode_are_scoped():
    box = repo({"a.ts": "export const a = 1;\n"})
    kind, said = shell(box, "printf 'console.log(6);\\n' > 'my file.ts' && printf 'console.log(7);\\n' > 'café.ts'")
    assert kind == "report" and "my file.ts" in said and "café.ts" in said, said


def test_an_unproven_rename_is_not_checked_and_its_siblings_still_are():
    legacy = "console.log('legacy');\nexport const x = 1;\n"
    box = repo({"x.ts": legacy, "a.ts": "export const a = 1;\n", "b.ts": "export const a = 1;\n",
                "sibling.ts": "export const s = 1;\n"})
    # The copy is made before the original goes, so it cannot inherit the original's inode.
    kind, said = shell(box, "cp x.ts x2.ts && printf 'export const z = 1;\\n' >> x2.ts && rm x.ts"
                            " && cp a.ts c.ts && rm a.ts b.ts && printf 'console.log(8);\\n' >> sibling.ts")
    assert kind == "report" and "sibling.ts" in said and f"{DEBUG}: L2" in said, said
    assert "x2.ts not checked" in said and "may have become it" in said, said
    assert "c.ts not checked" in said and "more than one deleted file" in said, said


WITHOUT_BIRTHTIME = r'''
import os, runpy, sys
sys.path.insert(0, os.path.dirname(sys.argv[1]))
from clean_code import snapshot
real = snapshot.identity
snapshot.identity = lambda info: real(info)[:2] + [None]
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


def shell_without_birthtime(box: Path, command: str) -> tuple:
    """The real pre and post hooks around a real command, on a filesystem that keeps no birth time, as Linux."""
    shim = Path(tempfile.mkdtemp()) / "no_birthtime.py"
    shim.write_text(WITHOUT_BIRTHTIME)
    payload = shell_payload(box, command)
    env = {"CLAUDE_PROJECT_DIR": str(box), "PATH": os.environ["PATH"], "TMPDIR": str(SHELL_STATE)}

    def run(mode: str, body: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(shim), str(ENTRY), mode], input=json.dumps(body), capture_output=True,
                              text=True, env=env, cwd=str(box))

    assert run("pre", payload).returncode == 0
    subprocess.run(command, shell=True, cwd=str(box), capture_output=True)
    return outcome(run("post", {**payload, "hook_event_name": "PostToolUse"}))


def test_without_birth_times_only_content_proves_a_rename_and_same_path_edits_are_checked():
    legacy = "console.log('legacy');\nexport const a = 1;\n"
    box = repo({"a.ts": legacy, "b.ts": "export const b = 1;\n", "c.ts": legacy.replace("a = 1", "c = 1"),
                "d.ts": "export const d = 1;\n", "e.ts": "export const d = 1;\n", "f.ts": "export const f: number = 1;\n"})
    assert shell_without_birthtime(box, "mv a.ts moved.ts") == ("silent", ""), "identical content is a pure rename"
    kind, said = shell_without_birthtime(box, "mv c.ts c2.ts && printf 'console.log(1);\\n' >> c2.ts")
    assert kind == "context" and "c2.ts not checked" in said, "a rename with an edit is unproven"
    kind, said = shell_without_birthtime(box, "mv moved.ts b.ts")
    assert kind == "context" and "b.ts not checked" in said and "moved over it" in said, said
    kind, said = shell_without_birthtime(box, "cp d.ts copy.ts && rm d.ts e.ts")
    assert kind == "context" and "copy.ts not checked" in said and "more than one deleted file" in said, said
    kind, said = shell_without_birthtime(box, "printf 'console.log(2);\\n' > genuine.ts")
    assert kind == "report" and "genuine.ts" in said and f"{DEBUG}: L1" in said, "a new file beside no deletion is new"
    kind, said = shell_without_birthtime(box, "sed -i.orig 's/: number = 1/ = v as any/' f.ts && rm f.ts.orig"
                                              " && printf 'console.log(3);\\n' >> f.ts")
    assert kind == "report" and "f.ts" in said and f"{WEAK}: L1" in said and f"{DEBUG}: L2" in said, said


def test_an_inode_a_deleted_file_freed_proves_nothing_without_a_birth_time():
    from clean_code.snapshot import Content, attributed
    old, new = b"console.log('legacy');\n", b"export const fresh = 1;\n"
    reused = [1, 42, None]
    [arrival] = attributed({"x.ts": Content(old, None, reused)}, {}, {"c.ts": Content(new, None, reused)})[:1]
    assert arrival.path == "c.ts" and arrival.doubt, "a reused inode is not a move"
    [overwritten] = attributed({"x.ts": Content(old, None, reused)},
                               {"b.ts": (Content(new, None, [1, 7, None]), Content(new + old, None, reused))}, {})[:1]
    assert overwritten.path == "b.ts" and "moved over it" in overwritten.doubt
    kept = [1, 42, 1_700_000_000.5]
    [moved] = attributed({"x.ts": Content(old, None, kept)}, {}, {"c.ts": Content(old + new, None, kept)})
    assert moved.path == "c.ts" and moved.before == old and not moved.doubt, "with a birth time the move is proven"


def test_a_new_file_beside_an_unpaired_deletion_is_not_checked_whatever_it_holds():
    old = "".join(f"console.log('legacy {i}');\n" for i in range(30))
    box = repo({"old.ts": old, "a.ts": "export const a = 1;\n", "b.ts": "export const b = 2;\n"})
    kind, said = shell(box, "cp old.ts new.ts && printf 'console.log(9);\\n' > new.ts && rm old.ts")
    assert kind == "context" and "new.ts not checked" in said and "L1" not in said, said
    kind, said = shell(box, "cp a.ts c.ts && cp b.ts d.ts && printf 'export const c = 3;\\n' > c.ts"
                            " && printf 'export const d = 4;\\n' > d.ts && rm a.ts b.ts")
    assert kind == "context" and "c.ts not checked" in said and "d.ts not checked" in said, said
    kind, said = shell(box, "printf 'console.log(10);\\n' > genuine.ts")
    assert kind == "report" and "genuine.ts" in said and f"{DEBUG}: L1" in said, "no deletion: a new file is new"
    assert shell(box, "rm genuine.ts") == ("silent", ""), "a deletion alone introduces nothing"


AUDIT = r'''
import builtins, functools, io, os, runpy, subprocess, sys
watch, log, entry, mode = sys.argv[1:5]
real_open, real_os_open = builtins.open, os.open


def seen(path):
    if isinstance(path, int):
        return
    full = os.path.realpath(os.path.abspath(os.fsdecode(os.fspath(path))))
    if full == watch or full.startswith(watch + os.sep):
        with real_open(log, "a") as out:
            out.write(full + "\n")


def call(real, path, *args, **kwargs):
    if kwargs.get("follow_symlinks", True):
        seen(path)
    return real(path, *args, **kwargs)


def audited(real):
    # A partial is never bound as a method, so pathlib may keep it as a class attribute on older Pythons.
    return functools.partial(call, real)


builtins.open = io.open = audited(real_open)
os.open = audited(real_os_open)
for name in ("stat", "utime", "chmod", "truncate", "listdir", "scandir"):
    setattr(os, name, audited(getattr(os, name)))
real_run = subprocess.run


def run(args, *rest, **kwargs):
    for word in list(args) + [kwargs.get("cwd") or "."]:
        if isinstance(word, (str, os.PathLike)) and os.sep in os.fspath(word):
            seen(word)
    return real_run(args, *rest, **kwargs)


subprocess.run = run
sys.argv = [entry, mode]
runpy.run_path(entry, run_name="__main__")
'''


def audited_hook(mode: str, payload: dict, box: Path, watch: Path, state: Path) -> tuple:
    """The hook, recording every open, stat, utime or chmod that reaches watch, links followed included."""
    shim = Path(tempfile.mkdtemp()) / "audit.py"
    shim.write_text(AUDIT)
    log = shim.with_name("touched.log")
    log.write_text("")
    proc = subprocess.run([sys.executable, str(shim), os.path.realpath(watch), str(log), str(ENTRY), mode],
                          input=json.dumps(payload), capture_output=True, text=True, cwd=str(box),
                          env={"CLAUDE_PROJECT_DIR": str(box), "PATH": os.environ["PATH"], "TMPDIR": str(state)})
    return proc, log.read_text().split()


def untouched(path: Path) -> tuple:
    info = os.lstat(path)
    return path.read_bytes() if path.is_file() else None, info.st_mtime_ns, info.st_mode


def the_record(state: Path, payload: dict) -> Path:
    from clean_code.snapshot import record_key
    [record] = [p for p in (state / "clean-code-snapshots").iterdir() if p.name.endswith(record_key(payload))]
    return record


def test_a_manifest_redirected_to_another_worktree_is_not_checked_and_never_read():
    state = Path(tempfile.mkdtemp())
    a, b = repo({"a.ts": "export const a = 1;\n"}), repo({"a.ts": "export const a = 1;\n"})
    payload = shell_payload(a, "true")
    assert hook("pre", payload, a, state).returncode == 0
    manifest = the_record(state, payload) / "manifest.json"
    data = json.loads(manifest.read_text())
    data["root"] = subprocess.run(["git", "-C", str(b), "rev-parse", "--show-toplevel"], capture_output=True,
                                  text=True).stdout.strip()
    manifest.write_text(json.dumps(data))
    with open(b / "a.ts", "a") as handle:
        handle.write("console.log(44);\n")
    before = untouched(b / "a.ts")
    proc, touched = audited_hook("post", {**payload, "hook_event_name": "PostToolUse"}, a, b, state)
    kind, said = outcome(proc)
    assert kind == "context" and "another worktree" in said, said
    assert touched == [], touched
    assert str(b) not in said and os.path.realpath(b) not in said and untouched(b / "a.ts") == before


def test_every_binding_field_of_a_snapshot_is_checked_before_anything_is_read():
    outside = Path(tempfile.mkdtemp())
    (outside / "secret.ts").write_text("console.log('outside');\n")

    def rename_tag(record, data):
        tag, rest = record.name.split(".", 1)
        record.rename(record.with_name(f"{'f' * 16}.{rest}"))

    def link_blob(record, data):
        (record / "blobs/0").unlink()
        (record / "blobs/0").symlink_to(outside / "secret.ts")

    corruptions = {
        "tag": (rename_tag, "another worktree"),
        "identity": (lambda r, d: d["invocation"].__setitem__(4, "toolu_other"), "does not belong"),
        "key": (lambda r, d: d.__setitem__("key", "0" * 32), "does not belong"),
        "blob name": (lambda r, d: d["kept"].__setitem__("dirty.ts", "../../secret"), "damaged"),
        "blob link": (link_blob, "damaged"),
        "parent path": (lambda r, d: d["clean"].__setitem__("../secret.ts", "0" * 40), "damaged"),
        "absolute path": (lambda r, d: d["kept"].__setitem__(str(outside / "secret.ts"), "0"), "damaged"),
        "root type": (lambda r, d: d.__setitem__("root", ["x"]), "another worktree"),
    }
    for name, (corrupt, reason) in corruptions.items():
        state = Path(tempfile.mkdtemp())
        box = repo({"a.ts": "export const a = 1;\n"})
        (box / "dirty.ts").write_text("export const d = 1;\n")
        payload = shell_payload(box, "true")
        assert hook("pre", payload, box, state).returncode == 0
        record = the_record(state, payload)
        data = json.loads((record / "manifest.json").read_text())
        corrupt(record, data)
        if name not in ("tag", "blob link"):
            (record / "manifest.json").write_text(json.dumps(data))
        (box / "dirty.ts").write_text("console.log(1);\n")
        proc, touched = audited_hook("post", {**payload, "hook_event_name": "PostToolUse"}, box, outside, state)
        kind, said = outcome(proc)
        assert kind == "context" and "not checked" in said and reason in said, (name, said)
        assert touched == [], (name, touched)


def test_no_state_operation_follows_a_link_out_of_the_state_directory():
    from clean_code.snapshot import record_key
    outside = Path(tempfile.mkdtemp())
    (outside / "target.ts").write_text("do not touch\n")
    (outside / "dir").mkdir()
    (outside / "dir/keep.ts").write_text("keep\n")
    old = 1_600_000_000
    os.utime(outside / "target.ts", (old, old))
    target_before, dir_before = untouched(outside / "target.ts"), untouched(outside / "dir/keep.ts")
    state = Path(tempfile.mkdtemp())
    box = repo({"a.ts": "export const a = 1;\n"})
    (box / "dirty.ts").write_text("export const d = 1;\n")
    denied = shell_payload(box, "never ran")
    assert hook("pre", denied, box, state).returncode == 0
    records = state / "clean-code-snapshots"
    key = record_key(denied)
    (records / f".tomb.{key}").symlink_to(outside / "target.ts")
    hostile = {".tmp.evil": outside / "target.ts", f".claimed.{key}.x": outside / "dir", "garbage-link": outside / "dir",
               f".shared.{'1' * 32}": outside / "target.ts"}
    for name, target in hostile.items():
        (records / name).symlink_to(target)
        if os.utime in os.supports_follow_symlinks:
            os.utime(records / name, (old, old), follow_symlinks=False)
    expire(state)
    proc, touched = audited_hook("pre", shell_payload(box, "true"), box, outside, state)
    assert proc.returncode == 0 and proc.stderr == "", proc
    assert touched == [], touched
    assert untouched(outside / "target.ts") == target_before and untouched(outside / "dir/keep.ts") == dir_before
    tomb = os.lstat(records / f".tomb.{key}")
    assert not stat_is_link(tomb), "the tombstone replaced the link instead of touching its target"
    left = set(os.listdir(records))
    if os.utime in os.supports_follow_symlinks:
        assert not left & {".tmp.evil", f".claimed.{key}.x", "garbage-link"}, left
    (records / f"{'a' * 16}.1.{int(time.time()) + 600}.{key}").symlink_to(outside / "dir")
    proc, touched = audited_hook("post", {**denied, "hook_event_name": "PostToolUse"}, box, outside, state)
    kind, said = outcome(proc)
    assert kind == "context" and "not checked" in said and touched == [], (said, touched)
    assert untouched(outside / "dir/keep.ts") == dir_before


def stat_is_link(info: os.stat_result) -> bool:
    import stat as stat_module
    return stat_module.S_ISLNK(info.st_mode)


def corrupted_post(box: Path, watch: Path, corrupt, prepare=None) -> tuple:
    """A real pre snapshot, the manifest corrupted by hand, then the post run under the audit shim."""
    state = Path(tempfile.mkdtemp())
    payload = shell_payload(box, "true")
    assert hook("pre", payload, box, state).returncode == 0
    manifest = the_record(state, payload) / "manifest.json"
    data = json.loads(manifest.read_text())
    corrupt(data)
    manifest.write_text(json.dumps(data))
    if prepare:
        prepare()
    proc, touched = audited_hook("post", {**payload, "hook_event_name": "PostToolUse"}, box, watch, state)
    return outcome(proc), touched


def test_a_manifest_path_that_ends_in_a_link_is_never_followed():
    b = repo({"secret.ts": "export const s = 1;\n"})
    (b / "secret.ts").write_text("console.log('dirty in b');\n")
    a = repo({"a.ts": "export const a = 1;\n", "sub/x.ts": "export const x = 1;\n"})
    (a / "escape").symlink_to(b)
    (a / "inlink").symlink_to(a / "sub")
    before = untouched(b / "secret.ts")
    for name in ("escape", "inlink"):
        (kind_, said), touched = corrupted_post(a, b, lambda d: d["inner"].__setitem__(name, "a" * 64))
        assert kind_ == "context" and "damaged (inner)" in said, (name, said)
        assert touched == [], (name, touched)
        assert str(b) not in said and os.path.realpath(b) not in said
    assert untouched(b / "secret.ts") == before
    nested = repo({"a.ts": "export const a = 1;\n"})
    repo({"inner.ts": "export const i = 1;\n"}, nested / "inner")
    kind_, said = shell(nested, "printf 'console.log(1);\\n' >> inner/inner.ts")
    assert kind_ == "context" and "inner not checked" in said, "a real nested repository is still compared"


def test_a_file_field_whose_path_is_now_a_link_is_rejected_unread():
    outside = Path(tempfile.mkdtemp())
    (outside / "target.ts").write_text("console.log('outside');\n")
    values = {"clean": "0" * 40, "kept": "0", "digests": "a" * 64}
    for field, value in values.items():
        for target in (outside / "target.ts", "a.ts"):
            box = repo({"a.ts": "export const a = 1;\n"})
            (box / "dirty.ts").write_text("export const d = 1;\n")
            (box / "evil.ts").symlink_to(target)

            def corrupt(data, field=field, value=value):
                data[field]["evil.ts"] = value
                data["ids"]["evil.ts"] = [1, 2, None]

            (kind_, said), touched = corrupted_post(box, outside, corrupt)
            assert kind_ == "context" and f"damaged ({field})" in said, (field, target, said)
            assert touched == [], (field, target, touched)


def test_a_link_the_snapshot_recorded_is_inspected_without_reading_its_target():
    outside = Path(tempfile.mkdtemp())
    (outside / "target.ts").write_text("export const t = 1;\n")
    box = Path(tempfile.mkdtemp()) / "repo"
    box.mkdir()
    (box / "link.ts").symlink_to(outside / "target.ts")
    repo({"a.ts": "export const a = 1;\n"}, box)
    state = Path(tempfile.mkdtemp())
    payload = shell_payload(box, "append through the link")
    assert hook("pre", payload, box, state).returncode == 0
    with open(box / "link.ts", "a") as handle:
        handle.write("console.log(1);\n")
    proc, touched = audited_hook("post", {**payload, "hook_event_name": "PostToolUse"}, box, outside, state)
    kind_, said = outcome(proc)
    assert kind_ == "context" and "link.ts not checked" in said and touched == [], (said, touched)


def test_a_link_whose_target_predates_1970_is_a_valid_unchanged_link():
    from clean_code.snapshot import is_link_signature, outside_link
    outside = Path(tempfile.mkdtemp()) / "old.ts"
    outside.write_text("export const old = 1;\n")
    os.utime(outside, ns=(0, -1_000_000_000))
    if os.stat(outside).st_mtime_ns != -1_000_000_000:
        print("  skipped: this filesystem cannot keep a time before 1970")
        return
    box = Path(tempfile.mkdtemp()) / "repo"
    box.mkdir()
    (box / "link.ts").symlink_to(outside)
    repo({"a.ts": "export const a = 1;\n"}, box)
    written = outside_link(Path(os.path.realpath(box)), "link.ts")
    assert written[1] == -1_000_000_000 and is_link_signature(json.loads(json.dumps(written))), written
    state = Path(tempfile.mkdtemp())
    payload = shell_payload(box, "true")
    assert hook("pre", payload, box, state).returncode == 0
    before = untouched(outside)
    proc, touched = audited_hook("post", {**payload, "hook_event_name": "PostToolUse"}, box, outside.parent, state)
    assert outcome(proc) == ("silent", ""), proc
    assert touched == [] and untouched(outside) == before


def test_link_and_identity_signatures_have_exactly_the_shape_the_writer_gives():
    from clean_code.snapshot import identity, is_identity, is_link_signature, outside_link
    root = Path(tempfile.mkdtemp())
    outside = Path(tempfile.mkdtemp()) / "t.ts"
    outside.write_text("x\n")
    (root / "l.ts").symlink_to(outside)
    (root / "gone.ts").symlink_to(outside.with_name("missing.ts"))
    written = [outside_link(Path(os.path.realpath(root)), name) for name in ("l.ts", "gone.ts")]
    for signature in written + [None]:
        assert is_link_signature(json.loads(json.dumps(signature))), signature
    for bad in ([], [1], [1, 2], [1, 2, 3, 4], ["1", 2, 3], [True, 2, 3], [1, True, 3], [1.0, 2, 3], [-1, 2, 3],
                [1, 2, -1], ["missing", 1], {}, "x", 7):
        assert not is_link_signature(bad), bad
    assert is_link_signature([1, -1, 3]) and is_link_signature([0, 0, 0]), "only the time may be negative"
    assert is_identity(json.loads(json.dumps(identity(os.lstat(outside)))))
    for bad in ([], [1, 2], [1, 2, None, 4], [True, 2, None], [1, 2, "x"], [1, 2, 3], ["1", 2, None]):
        assert not is_identity(bad), bad


def test_a_malformed_manifest_field_fails_before_the_worktree_is_compared():
    import tempfile as tempfile_module
    from clean_code import snapshot
    state = Path(tempfile.mkdtemp())
    box = repo({"a.ts": "export const a = 1;\n"})

    class Compared(Exception):
        pass

    def refuse(record):
        raise Compared

    saved = (snapshot.compare, tempfile_module.tempdir)
    snapshot.compare = refuse
    try:
        for links, expected in (([], "damaged (links)"), ([1, 2], "damaged (links)"), ([1, 2, 3, 4], "damaged (links)"),
                                (["1", 2, 3], "damaged (links)"), ([True, 2, 3], "damaged (links)"), ([1, 2, 3], None)):
            payload = shell_payload(box, "true")
            tempfile_module.tempdir = None
            assert hook("pre", payload, box, state).returncode == 0
            manifest = the_record(state, payload) / "manifest.json"
            data = json.loads(manifest.read_text())
            data["links"]["a.ts"] = links
            manifest.write_text(json.dumps(data))
            tempfile_module.tempdir = str(state)
            try:
                snapshot.changes({**payload, "hook_event_name": "PostToolUse"})
                raise AssertionError(f"{links} reached neither the boundary nor comparison")
            except snapshot.Unsupported as exc:
                assert expected and expected in str(exc), (links, exc)
            except Compared:
                assert expected is None, f"{links} reached comparison"
    finally:
        snapshot.compare, tempfile_module.tempdir = saved
    (kind_, said), touched = corrupted_post(box, box, lambda d: d["links"].__setitem__("a.ts", []))
    assert kind_ == "context" and "damaged (links)" in said, said


def test_a_write_never_reads_outside_the_project():
    import builtins
    import io
    from clean_code import hooks as hooks_module
    project = Path(tempfile.mkdtemp())
    outside = Path(tempfile.mkdtemp()) / "typed.ts"
    typed = "export function load(id: string, strict: boolean): Row {\n  return rows[id];\n}\n"
    outside.write_text(typed)
    (project / "inside.ts").write_text(typed)
    (project / "out-link.ts").symlink_to(outside)
    (project / "in-link.ts").symlink_to(project / "inside.ts")
    untyped = typed.replace("id: string, strict: boolean): Row", "id, strict)")

    def refuse(*args, **kwargs):
        raise AssertionError(f"read {args[:1]}")

    saved = (hooks_module.read_lines, builtins.open, io.open, Path.read_text, Path.read_bytes)
    hooks_module.read_lines = builtins.open = io.open = Path.read_text = Path.read_bytes = refuse
    try:
        for target in (outside, project / "out-link.ts", project / "in-link.ts"):
            write = {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": untyped}}
            assert cc.pre_check(write, project) is None, target
    finally:
        hooks_module.read_lines, builtins.open, io.open, Path.read_text, Path.read_bytes = saved
    write = {"tool_name": "Write", "tool_input": {"file_path": str(project / "inside.ts"), "content": untyped}}
    assert "removes type annotations" in cc.pre_check(write, project)
    loosen = {"tool_name": "Write", "tool_input": {"file_path": str(outside.with_name("tsconfig.json")),
                                                  "content": json.dumps({"strict": False})}}
    assert "strict mode" in cc.pre_check(loosen, project), "new content is judged even where old content is not read"


def expire(state: Path) -> None:
    """Move every live record's lease into the past, as if its command had run out of time."""
    from clean_code.snapshot import RECORD_NAME
    records = state / "clean-code-snapshots"
    for entry in os.listdir(records):
        m = RECORD_NAME.match(entry)
        if m:
            past = int(time.time()) - 60
            os.rename(records / entry, records / f"{m.group(1)}.{past - 600}.{past}.{m.group(4)}")


def test_a_denied_command_stops_blocking_the_worktree_once_its_lease_ends():
    state = Path(tempfile.mkdtemp())
    box = repo({"a.ts": "export const a = 1;\n"})
    denied = shell_payload(box, "never ran")
    assert hook("pre", denied, box, state).returncode == 0
    during = shell_payload(box, "writes")
    assert hook("pre", during, box, state).returncode == 0
    (box / "during.ts").write_text("console.log(1);\n")
    kind, said = finish(during, box, state=state)
    assert kind == "context" and "at the same time" in said, "a live lease still blocks attribution"
    expire(state)
    after = shell_payload(box, "writes")
    assert hook("pre", after, box, state).returncode == 0
    records = os.listdir(state / "clean-code-snapshots")
    assert [e for e in records if e.startswith(".tomb.")] and not [e for e in records if not e.startswith(".")][1:]
    (box / "after.ts").write_text("console.log(2);\n")
    kind, said = finish(after, box, state=state)
    assert kind == "report" and "after.ts" in said and f"{DEBUG}: L1" in said, said
    kind, said = finish(denied, box, state=state)
    assert kind == "context" and "expired" in said, said
    kind, said = finish(denied, box, state=state)
    assert kind == "context" and "not checked" in said, "a second late post is still not clean"


def test_expired_and_old_state_stays_bounded_and_leaves_room_for_new_commands():
    from clean_code.snapshot import TOMBSTONE_LIMIT
    state = Path(tempfile.mkdtemp())
    records = state / "clean-code-snapshots"
    records.mkdir(mode=0o700)
    past = int(time.time()) - 60
    for i in range(70):
        stale = records / f"{'ab' * 8}.{past - 600}.{past}.{i:032x}"
        (stale / "blobs").mkdir(parents=True)
        (stale / "blobs/0").write_bytes(b"x" * 4096)
    old = time.time() - 2 * 24 * 3600
    for i in range(TOMBSTONE_LIMIT + 100):
        tomb = records / f".tomb.{i:032x}"
        tomb.touch()
        if i < 50:
            os.utime(tomb, (old, old))
    box = repo({"a.ts": "export const a = 1;\n"})
    kind, said = shell_with_state(box, "printf 'console.log(1);\\n' >> a.ts", state)
    assert kind == "report" and f"{DEBUG}: L2" in said, said
    left = os.listdir(records)
    assert not [e for e in left if not e.startswith(".")], "expired records hold no baseline"
    assert len([e for e in left if e.startswith(".tomb.")]) <= TOMBSTONE_LIMIT


def shell_with_state(box: Path, command: str, state: Path) -> tuple:
    payload = shell_payload(box, command)
    assert hook("pre", payload, box, state).returncode == 0
    ran = subprocess.run(command, shell=True, cwd=str(box), capture_output=True)
    return finish(payload, box, ran.returncode, state)


def test_malformed_snapshot_state_never_crashes_and_never_passes():
    state = Path(tempfile.mkdtemp())
    records = state / "clean-code-snapshots"
    records.mkdir(mode=0o700)
    (records / "garbage").write_text("x")
    (records / "a.b").write_text("x")
    (records / "0123456789abcdef.x.y.z").mkdir()
    (records / "weird dir").mkdir()
    box = repo({"a.ts": "export const a = 1;\n"})
    kind, said = shell_with_state(box, "printf 'console.log(1);\\n' >> a.ts", state)
    assert kind == "report" and f"{DEBUG}: L2" in said, said
    for damage in ("manifest", "blob"):
        (box / "dirty.ts").write_text("export const d = 1;\n")
        payload = shell_payload(box, "damaged")
        assert hook("pre", payload, box, state).returncode == 0
        [record] = [records / e for e in os.listdir(records) if e.endswith(payload_key(payload))]
        if damage == "manifest":
            (record / "manifest.json").write_text('{"clean": ')
        else:
            for blob in (record / "blobs").iterdir():
                blob.unlink()
        (box / "dirty.ts").write_text("export const d = 2;\n")
        kind, said = finish(payload, box, state=state)
        assert kind == "context" and "not checked" in said, (damage, said)


def payload_key(payload: dict) -> str:
    from clean_code.snapshot import record_key
    return record_key(payload)


def test_codex_keeps_the_snapshot_of_the_original_exec_through_write_stdin():
    box = repo({"a.ts": "export const a = 1;\n"})
    start = shell_payload(box, "python3 -i", turn_id="turn-1")
    assert hook("pre", start, box).returncode == 0
    records = sorted(os.listdir(SHELL_STATE / "clean-code-snapshots"))
    stdin = {**start, "tool_name": "write_stdin", "tool_use_id": "toolu_stdin", "tool_input": {"chars": "x\n"}}
    assert hook("pre", stdin, box).returncode == 0
    assert sorted(os.listdir(SHELL_STATE / "clean-code-snapshots")) == records
    (box / "a.ts").write_text("export const a = 1;\nconsole.log(1);\n")
    kind, said = finish(start, box, 1)
    assert kind == "report" and f"{DEBUG}: L2" in said, said


def test_snapshot_state_is_private_and_bounded():
    state = Path(tempfile.mkdtemp())
    box = repo({"a.ts": "export const a = 1;\n"})
    payload = shell_payload(box, "true")
    assert hook("pre", payload, box, state).returncode == 0
    records = state / "clean-code-snapshots"
    assert records.stat().st_mode & 0o777 == 0o700
    past = int(time.time()) - 3 * 3600
    stale = records / f"{'de' * 8}.{past}.{past + 1}.0123456789abcdef0123456789abcdef"
    stale.mkdir()
    assert hook("pre", shell_payload(box, "true"), box, state).returncode == 0
    assert not stale.exists() and (records / ".tomb.0123456789abcdef0123456789abcdef").exists()
    assert finish(payload, box, state=state) == ("silent", "")
    assert not [e for e in os.listdir(records) if e.endswith(payload["tool_use_id"])]


def test_the_installer_covers_shell_writes_for_both_agents_and_migrates_old_matchers():
    home = fresh_home()
    settings = home / ".claude/settings.json"
    check = home / ".clean-code/clean_check.py"
    old = {"theme": "dark", "hooks": {
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|Bash", "hooks": [{"type": "command", "command": f'python3 "{check}" pre'}]},
                       {"matcher": "Bash", "hooks": [{"type": "command", "command": "somebody-elses-hook"}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f'python3 "{check}" post'}]}],
        "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "somebody-elses-failure"}]}]}}
    settings.write_text(json.dumps(old))
    for _ in range(2):
        assert run_installer(home, "--no-parser")[0] == 0
    matchers = {event: [g["matcher"] for g in ours(home, settings, event)]
                for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}
    assert matchers == {"PreToolUse": ["Edit|Write|Bash|PowerShell"], "PostToolUse": ["Edit|Write|Bash|PowerShell"],
                        "PostToolUseFailure": ["Bash|PowerShell"]}, matchers
    codex = home / ".codex/hooks.json"
    assert [g["matcher"] for g in ours(home, codex, "PostToolUse")] == ["Bash|apply_patch"]
    assert [g["matcher"] for g in ours(home, codex, "PreToolUse")] == ["Bash|apply_patch"]
    assert "PostToolUseFailure" not in json.loads(codex.read_text())["hooks"]
    foreign = [g["hooks"][0]["command"] for g in hook_groups(settings, "PostToolUseFailure") if not group_is_ours(g, check)]
    assert foreign == ["somebody-elses-failure"] and json.loads(settings.read_text())["theme"] == "dark"
    [failure] = ours(home, settings, "PostToolUseFailure")
    box = repo({"a.ts": "export const a = 1;\n"})
    payload = shell_payload(box, "printf 'console.log(1);\\n' >> a.ts; exit 3")
    run = lambda mode, body: subprocess.run(failure["hooks"][0]["command"].replace(" post", f" {mode}"), shell=True,
                                            input=json.dumps(body), capture_output=True, text=True, cwd=str(box),
                                            env={"PATH": os.environ["PATH"], "TMPDIR": str(SHELL_STATE)})
    assert run("pre", payload).returncode == 0
    subprocess.run(payload["tool_input"]["command"], shell=True, cwd=str(box))
    proc = run("post", {**payload, "hook_event_name": "PostToolUseFailure"})
    assert proc.returncode == 2 and f"{DEBUG}: L2" in proc.stderr, proc
    assert run_installer(home, "--uninstall")[0] == 0
    assert all(not ours(home, settings, e) for e in ("PreToolUse", "PostToolUse", "PostToolUseFailure"))
    assert [g["hooks"][0]["command"] for g in hook_groups(settings, "PostToolUseFailure")] == ["somebody-elses-failure"]


def group_is_ours(group, check: Path) -> bool:
    from clean_code.install import group_is_ours as owned
    return owned(group, check)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
