"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { resolveMnemoBinary, buildInitArgs, buildUninstallArgs, buildSpawnPlan } = require("../lib/runMnemo");


function _mktmpBin(name) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mnemo-test-"));
  const file = path.join(dir, name);
  fs.writeFileSync(file, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  return { dir, file };
}


test("resolveMnemoBinary skips the wrapper's own bin dir", () => {
  const wrapper = _mktmpBin("mnemo");
  const real = _mktmpBin("mnemo");
  const env = { PATH: [wrapper.dir, real.dir].join(":") };
  const bin = resolveMnemoBinary({ env, platform: "linux", selfBinDir: wrapper.dir });
  assert.equal(bin, path.join(real.dir, "mnemo"));
});


test("resolveMnemoBinary skips any directory under _npx/", () => {
  const npxDir = fs.mkdtempSync(path.join(os.tmpdir(), "_npx-"));
  // ensure dir basename includes _npx segment
  const npxInner = path.join(npxDir, "_npx", "abc", "node_modules", ".bin");
  fs.mkdirSync(npxInner, { recursive: true });
  fs.writeFileSync(path.join(npxInner, "mnemo"), "", { mode: 0o755 });
  const real = _mktmpBin("mnemo");
  const env = { PATH: [npxInner, real.dir].join(":") };
  const bin = resolveMnemoBinary({ env, platform: "linux", selfBinDir: null });
  assert.equal(bin, path.join(real.dir, "mnemo"));
});


test("resolveMnemoBinary returns null when no real mnemo on PATH", () => {
  const wrapper = _mktmpBin("mnemo");
  const env = { PATH: wrapper.dir };
  const bin = resolveMnemoBinary({ env, platform: "linux", selfBinDir: wrapper.dir });
  assert.equal(bin, null);
});


test("buildInitArgs forms init command with project scope", () => {
  const args = buildInitArgs({ scope: "project", quiet: false, yes: true });
  assert.deepEqual(args, ["init", "--project", "--yes"]);
});


test("buildUninstallArgs forms uninstall command", () => {
  const args = buildUninstallArgs({ scope: "global", quiet: true, yes: true });
  assert.deepEqual(args, ["uninstall", "--yes", "--quiet"]);
});


test("buildSpawnPlan uses the resolved binary directly when available", () => {
  const plan = buildSpawnPlan("/usr/local/bin/mnemo", ["init", "--yes"]);
  assert.deepEqual(plan, { cmd: "/usr/local/bin/mnemo", args: ["init", "--yes"] });
});


test("buildSpawnPlan falls back to python3 -m mnemo when binary not found", () => {
  const plan = buildSpawnPlan(null, ["init", "--project", "--yes"]);
  assert.deepEqual(plan, { cmd: "python3", args: ["-m", "mnemo", "init", "--project", "--yes"] });
});
