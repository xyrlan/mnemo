"use strict";

const { execSync, spawnSync } = require("node:child_process");
const { probe } = require("./detect");


const PIN_SPEC = "mnemo>=0.12,<0.13";


function buildInstallCmd(installer, spec = PIN_SPEC) {
  switch (installer) {
    case "uv":       return `uv tool install '${spec}'`;
    case "pipx":     return `pipx install '${spec}'`;
    case "pip-user": return `python3 -m pip install --user '${spec}'`;
    default: throw new Error(`unknown installer: ${installer}`);
  }
}


function buildUpgradeCmd(installer) {
  switch (installer) {
    case "uv":       return "uv tool upgrade mnemo";
    case "pipx":     return "pipx upgrade mnemo";
    case "pip-user": return "python3 -m pip install --user --upgrade mnemo";
    default: throw new Error(`unknown installer: ${installer}`);
  }
}


function isAlreadyInstalled(probeFn = probe) {
  return probeFn("mnemo --version");
}


function runShell(cmd, { quiet = false } = {}) {
  const result = spawnSync("sh", ["-c", cmd], { stdio: quiet ? "ignore" : "inherit" });
  return result.status === null ? 1 : result.status;
}


function verifyOnPath() {
  try {
    execSync("mnemo --version", { stdio: "ignore" });
    return true;
  } catch (_e) {
    return false;
  }
}


function pathFixHint(installer) {
  if (installer === "pipx") return "Run `pipx ensurepath` and reopen your shell.";
  if (installer === "uv")   return "Run `uv tool update-shell` and reopen your shell.";
  if (installer === "pip-user") {
    if (process.platform === "win32") return "Add %APPDATA%\\Python\\Scripts to PATH.";
    return "Add ~/.local/bin to PATH (e.g. via your shell profile).";
  }
  return "Re-open your shell to refresh PATH.";
}


module.exports = {
  PIN_SPEC,
  buildInstallCmd,
  buildUpgradeCmd,
  isAlreadyInstalled,
  runShell,
  verifyOnPath,
  pathFixHint,
};
