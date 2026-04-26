"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { buildInstallCmd, buildUpgradeCmd, isAlreadyInstalled } = require("../lib/bootstrap");

const SPEC = "mnemo-claude>=0.13,<0.14";

test("buildInstallCmd uses uv tool install for uv", () => {
  assert.equal(buildInstallCmd("uv", SPEC), `uv tool install '${SPEC}'`);
});

test("buildInstallCmd uses pipx install for pipx", () => {
  assert.equal(buildInstallCmd("pipx", SPEC), `pipx install '${SPEC}'`);
});

test("buildInstallCmd uses pip --user for pip-user", () => {
  assert.equal(buildInstallCmd("pip-user", SPEC), `python3 -m pip install --user '${SPEC}'`);
});

test("buildUpgradeCmd uses pipx upgrade", () => {
  assert.equal(buildUpgradeCmd("pipx"), "pipx upgrade mnemo-claude");
});

test("buildUpgradeCmd uses uv tool upgrade", () => {
  assert.equal(buildUpgradeCmd("uv"), "uv tool upgrade mnemo-claude");
});

test("buildUpgradeCmd uses pip install --user --upgrade for pip-user", () => {
  assert.equal(buildUpgradeCmd("pip-user"), "python3 -m pip install --user --upgrade mnemo-claude");
});

test("isAlreadyInstalled returns true when probe finds mnemo on path", () => {
  const probeFn = (cmd) => cmd === "mnemo";
  assert.equal(isAlreadyInstalled(probeFn), true);
});

test("isAlreadyInstalled returns false when probe misses", () => {
  const probeFn = () => false;
  assert.equal(isAlreadyInstalled(probeFn), false);
});
