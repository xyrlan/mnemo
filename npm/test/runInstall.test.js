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
