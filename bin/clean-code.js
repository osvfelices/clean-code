#!/usr/bin/env node
// Thin front door for `npx`. The installer is install.sh; this only finds it and reports why it cannot run.
"use strict";

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join, dirname } = require("node:path");

const ROOT = dirname(__dirname);
const INSTALLER = join(ROOT, "install.sh");

const USAGE = `clean-code <command>

  install            install for every agent found on this machine
  install --claude   install for Claude Code only
  install --codex    install for Codex only
  uninstall          remove from every agent
  rules              list the rules and the languages each one holds for
  explain <rule>     what one rule means
  files <path...>    check paths and exit non-zero on a finding

Optional: pip install tree-sitter tree-sitter-language-pack
adds the two rules that need a syntax tree.`;

function python() {
  for (const candidate of ["python3", "python"]) {
    const probe = spawnSync(candidate, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"]);
    if (probe.status === 0) return candidate;
  }
  return null;
}

function run(command, args) {
  return spawnSync(command, args, { stdio: "inherit", cwd: process.cwd() }).status ?? 1;
}

function main(argv) {
  const [command, ...rest] = argv;
  if (!command || command === "--help" || command === "-h") {
    console.log(USAGE);
    return 0;
  }

  if (command === "install" || command === "uninstall") {
    if (!existsSync(INSTALLER)) {
      console.error(`clean-code: installer missing at ${INSTALLER}`);
      return 1;
    }
    const flags = command === "uninstall" ? ["--uninstall", ...rest] : rest;
    return run("bash", [INSTALLER, ...flags]);
  }

  const interpreter = python();
  if (!interpreter) {
    console.error("clean-code: python 3.9 or newer is required and was not found on PATH.");
    return 1;
  }
  return run(interpreter, [join(ROOT, "core", "clean_check.py"), command, ...rest]);
}

process.exit(main(process.argv.slice(2)));
