"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { buildInstallCmd, buildUpgradeCmd, isAlreadyInstalled, pathFixHint } = require("../lib/bootstrap");

const SPEC = "mnemo-claude>=0.14,<0.15";

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

test("isAlreadyInstalled returns true when resolver finds a real binary", () => {
  const resolverFn = () => "/usr/local/bin/mnemo";
  assert.equal(isAlreadyInstalled(resolverFn), true);
});

test("isAlreadyInstalled returns false when resolver returns null", () => {
  const resolverFn = () => null;
  assert.equal(isAlreadyInstalled(resolverFn), false);
});

test("pathFixHint pip-user on darwin uses python user-base when available", () => {
  const execFn = () => "/Users/x/Library/Python/3.14";
  const hint = pathFixHint("pip-user", { platform: "darwin", execFn });
  assert.match(hint, /\/Users\/x\/Library\/Python\/3\.14\/bin/);
});

test("pathFixHint pip-user on linux uses python user-base when available", () => {
  const execFn = () => "/home/x/.local";
  const hint = pathFixHint("pip-user", { platform: "linux", execFn });
  assert.match(hint, /\/home\/x\/\.local\/bin/);
});

test("pathFixHint pip-user on darwin falls back to Library/Python when python missing", () => {
  const execFn = () => { throw new Error("no python"); };
  const hint = pathFixHint("pip-user", { platform: "darwin", execFn });
  assert.match(hint, /~\/Library\/Python/);
  assert.doesNotMatch(hint, /\.local\/bin/);
});

test("pathFixHint pip-user on linux falls back to ~/.local/bin when python missing", () => {
  const execFn = () => { throw new Error("no python"); };
  const hint = pathFixHint("pip-user", { platform: "linux", execFn });
  assert.match(hint, /~\/\.local\/bin/);
});

test("pathFixHint pip-user on win32 uses python user-base when available", () => {
  const execFn = () => "C:\\Users\\x\\AppData\\Roaming\\Python";
  const hint = pathFixHint("pip-user", { platform: "win32", execFn });
  assert.match(hint, /C:\\Users\\x\\AppData\\Roaming\\Python\\Scripts/);
});

test("pathFixHint pip-user on win32 falls back to %APPDATA% when python missing", () => {
  const execFn = () => { throw new Error("no python"); };
  const hint = pathFixHint("pip-user", { platform: "win32", execFn });
  assert.match(hint, /%APPDATA%\\Python\\Scripts/);
});

test("pathFixHint pipx and uv pass through platform-agnostic", () => {
  assert.match(pathFixHint("pipx"), /pipx ensurepath/);
  assert.match(pathFixHint("uv"), /uv tool update-shell/);
});

test("runShell uses cmd.exe on win32 and sh elsewhere", () => {
  // We can't easily spawn either shell in tests across platforms, so just
  // exercise the platform branch via a lightweight stub-driven dry run.
  const childProcess = require("node:child_process");
  const original = childProcess.spawnSync;
  const calls = [];
  childProcess.spawnSync = function (cmd, args, opts) {
    calls.push({ cmd, args, opts });
    return { status: 0 };
  };
  try {
    // re-require to pick up monkeypatched spawnSync? No — bootstrap captured
    // spawnSync at import time. So instead, assert via the platform-aware
    // shape by importing a fresh copy with cache busting.
    delete require.cache[require.resolve("../lib/bootstrap")];
    const { runShell } = require("../lib/bootstrap");
    runShell("echo hi", { quiet: true, platform: "win32" });
    runShell("echo hi", { quiet: true, platform: "linux" });
    assert.equal(calls.length, 2);
    assert.equal(calls[0].cmd, "cmd.exe");
    assert.deepEqual(calls[0].args, ["/d", "/s", "/c", "echo hi"]);
    assert.equal(calls[1].cmd, "sh");
    assert.deepEqual(calls[1].args, ["-c", "echo hi"]);
  } finally {
    childProcess.spawnSync = original;
    delete require.cache[require.resolve("../lib/bootstrap")];
  }
});
