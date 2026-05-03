"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseFlags } = require("../lib/runInstall");

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
