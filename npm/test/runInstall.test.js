"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseFlags, resolveToolchain } = require("../lib/runInstall");

// resolveToolchain decides what mnemo needs before installing anything.
//
// The order used to be: detect Python, bail if absent, THEN pick an installer.
// That bailed on a machine with uv and no system Python -- even though uv
// provisions its own CPython, so it is exactly the tool that solves the
// problem. The installer that would have worked was never reached.

const PY = { version: { major: 3, minor: 11 }, command: "python3" };

test("uv alone is enough — it provisions its own CPython", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => "uv",
    detectPythonFn: () => null,
  });
  assert.equal(r.installer, "uv");
  assert.equal(r.error, undefined);
});

test("uv is preferred even when a system Python exists", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => "uv",
    detectPythonFn: () => PY,
  });
  assert.equal(r.installer, "uv");
  assert.deepEqual(r.python, PY);
});

test("pipx without a system Python is an error, not a silent failure", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => "pipx",
    detectPythonFn: () => null,
  });
  assert.match(r.error, /Python 3\.8\+/);
});

test("pip-user without a system Python is an error", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => "pip-user",
    detectPythonFn: () => null,
  });
  assert.match(r.error, /Python 3\.8\+/);
});

test("pipx with a system Python resolves", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => "pipx",
    detectPythonFn: () => PY,
  });
  assert.equal(r.installer, "pipx");
});

test("no installer at all reports the installer problem, not the Python one", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => null,
    detectPythonFn: () => PY,
  });
  assert.match(r.error, /installer/i);
});

test("neither installer nor Python names uv as the one-step fix", () => {
  const r = resolveToolchain({
    pickInstallerFn: () => null,
    detectPythonFn: () => null,
  });
  assert.match(r.error, /uv/);
});

test("parseFlags reads --project, --vault-root, --upgrade, --yes", () => {
  const f = parseFlags(["--project", "--vault-root", "/tmp/v", "--upgrade", "--yes"]);
  assert.equal(f.scope, "project");
  assert.equal(f.vaultRoot, "/tmp/v");
  assert.equal(f.upgrade, true);
  assert.equal(f.yes, true);
});

test("parseFlags accepts --local as alias for --project", () => {
  const f = parseFlags(["--local"]);
  assert.equal(f.scope, "project");
});

test("parseFlags warns on unknown flag", () => {
  const warnings = [];
  parseFlags(["--bogus"], { warn: (m) => warnings.push(m) });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /unknown flag: --bogus/);
});

test("parseFlags warns when --vault-root is missing its value", () => {
  const warnings = [];
  const f = parseFlags(["--vault-root"], { warn: (m) => warnings.push(m) });
  assert.equal(f.vaultRoot, null);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /--vault-root requires/);
});

test("parseFlags warns when --vault-root is followed by another flag", () => {
  const warnings = [];
  const f = parseFlags(["--vault-root", "--quiet"], { warn: (m) => warnings.push(m) });
  assert.equal(f.vaultRoot, null);
  assert.equal(f.quiet, true);
  assert.equal(warnings.length, 1);
});

test("parseFlags does not warn on positional non-flag tokens", () => {
  const warnings = [];
  parseFlags(["someval"], { warn: (m) => warnings.push(m) });
  assert.equal(warnings.length, 0);
});
