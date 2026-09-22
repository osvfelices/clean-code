import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clean_check as cc  # noqa: E402  (test-only import path)

ROOT = Path(tempfile.mkdtemp())
CFG = cc.Config.load(ROOT)


def rules_for(name: str, text: str) -> set[str]:
    path = ROOT / name
    path.write_text(text)
    return {v.rule for v in cc.check_file(path, CFG, ROOT)}


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
    assert "phase-label" not in rules_for("x3.tsx", "// Step 1 fields stay required until the album is complete\nconst a: number = 1;\n")
    assert "phase-label" not in rules_for("x4.tsx", "// Phase 0 of the scroll timeline runs from 0.1 to 0.38\nconst a: number = 1;\n")
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
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps({"tool_input": {"file_path": str(path)}}),
                          capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 2 and "debug output: L1" in proc.stderr
    again = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps({"tool_input": {"file_path": str(path)}}),
                           capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert again.returncode == 2 and "still open" in again.stderr and len(again.stderr) < 80


def test_autofix_touches_comments_only():
    path = ROOT / "fix.ts"
    path.write_text("// ---------- helpers ----------\n// map errors \u2014 onto the envelope \u00b7 then return\nconst s: string = \"a \u2014 b\";\nif (x) {\n  run();\n} // end if\n")
    assert cc.autofix(path) >= 3
    out = path.read_text()
    assert "----" not in out and "// end" not in out
    assert "// map errors, onto the envelope; then return" in out
    assert 'const s: string = "a \u2014 b";' in out



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
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps(payload),
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
    path.write_text("console.log('old');\nconst keep: number = 1;\nconst b = c as any;\n")
    payload = {"session_id": "scope1", "tool_input": {"file_path": str(path), "old_string": "const keep: number = 1;", "new_string": "const b = c as any;"}}
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps(payload),
                          capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert "loose type" in proc.stderr and ": L3" in proc.stderr, proc.stderr
    assert "debug output" not in proc.stderr, proc.stderr


def test_unlocatable_edit_still_checks_whole_file():
    path = ROOT / "whole.ts"
    path.write_text("console.log('x');\n")
    payload = {"session_id": "scope2", "tool_input": {"file_path": str(path)}}
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps(payload),
                          capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert "debug output: L1" in proc.stderr


def test_malformed_config_is_reported_not_swallowed():
    bad = Path(tempfile.mkdtemp())
    (bad / ".clean-code.json").write_text('{"ignore": ["a",]}')
    (bad / "x.ts").write_text("const a = b as any;\n")
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "files", "x.ts"],
                          capture_output=True, text=True, cwd=str(bad), env={"PATH": "/usr/bin:/bin"})
    assert "ignoring malformed" in proc.stderr, proc.stderr


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
    ("py", "except ValueError:\n    pass", "empty catch"),
]


def test_recall_on_the_edited_line():
    """Scoping must never hide a defect the edit itself introduced."""
    box = Path(tempfile.mkdtemp())
    for i, (ext, bad, needle) in enumerate(DEFECTS):
        host = "\n".join(f"v{j} = {j}" if ext == "py" else f"export const v{j}: number = {j};" for j in range(300))
        path = box / f"host{i}.{ext}"
        path.write_text(host + "\n" + bad + "\n")
        payload = {"session_id": f"recall{i}", "tool_input": {"file_path": str(path), "old_string": "absent", "new_string": bad}}
        proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps(payload),
                              capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(box), "PATH": "/usr/bin:/bin"})
        assert needle.lower() in proc.stderr.lower(), f"{bad!r} went unreported: {proc.stderr!r}"


def test_autofixable_defect_is_removed_rather_than_reported():
    box = Path(tempfile.mkdtemp())
    path = box / "banner.ts"
    path.write_text("export const a: number = 1;\n// ---------- section ----------\n")
    payload = {"session_id": "autofix1", "tool_input": {"file_path": str(path), "old_string": "absent", "new_string": "// ---------- section ----------"}}
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "post"], input=json.dumps(payload),
                          capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(box), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and proc.stderr == ""
    assert "----" not in path.read_text()


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
