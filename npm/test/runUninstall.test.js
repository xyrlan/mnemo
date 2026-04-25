"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseUninstallFlags, resolveUninstallScope } = require("../lib/runUninstall");

test("parseUninstallFlags reads --scope global", () => {
  const f = parseUninstallFlags(["--scope", "global", "--yes"]);
  assert.equal(f.scope, "global");
  assert.equal(f.yes, true);
});

test("parseUninstallFlags reads --scope both", () => {
  const f = parseUninstallFlags(["--scope", "both"]);
  assert.equal(f.scope, "both");
});

test("resolveUninstallScope auto-picks project when only project present", () => {
  const r = resolveUninstallScope(null, { project: true, global: false }, false);
  assert.equal(r, "project");
});

test("resolveUninstallScope auto-picks global when only global present", () => {
  const r = resolveUninstallScope(null, { project: false, global: true }, false);
  assert.equal(r, "global");
});

test("resolveUninstallScope errors on both-present + --yes + no flag", () => {
  assert.throws(() => resolveUninstallScope(null, { project: true, global: true }, true));
});

test("resolveUninstallScope respects explicit flag over detection", () => {
  const r = resolveUninstallScope("global", { project: true, global: true }, true);
  assert.equal(r, "global");
});
