"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parsePythonVersion, pickInstaller, isPep668 } = require("../lib/detect");

test("parsePythonVersion accepts CPython 3.11", () => {
  assert.deepEqual(parsePythonVersion("Python 3.11.4"), { major: 3, minor: 11, patch: 4 });
});

test("parsePythonVersion rejects below 3.8", () => {
  const v = parsePythonVersion("Python 3.7.16");
  assert.equal(v.major, 3);
  assert.equal(v.minor, 7);
});

test("parsePythonVersion returns null for garbage", () => {
  assert.equal(parsePythonVersion("not a version"), null);
});

test("pickInstaller prefers uv when present", () => {
  const fakeProbe = (cmd) => cmd === "uv" || cmd === "pipx" || cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "uv");
});

test("pickInstaller falls back to pipx when uv missing", () => {
  const fakeProbe = (cmd) => cmd === "pipx" || cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "pipx");
});

test("pickInstaller falls back to pip-user when only pip", () => {
  const fakeProbe = (cmd) => cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "pip-user");
});

test("pickInstaller returns null when nothing available", () => {
  const fakeProbe = () => false;
  assert.equal(pickInstaller(fakeProbe), null);
});

test("isPep668 detects externally-managed-environment", () => {
  const stderr = "error: externally-managed-environment\n× This environment is externally managed";
  assert.equal(isPep668(stderr), true);
});

test("isPep668 returns false on normal pip output", () => {
  assert.equal(isPep668("Successfully installed foo-1.0"), false);
});
