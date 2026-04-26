"use strict";

const { execSync, spawnSync } = require("node:child_process");
const { probe } = require("./detect");


const PIN_SPEC = "mnemo-claude>=0.13,<0.14";


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
    case "uv":       return "uv tool upgrade mnemo-claude";
    case "pipx":     return "pipx upgrade mnemo-claude";
    case "pip-user": return "python3 -m pip install --user --upgrade mnemo-claude";
    default: throw new Error(`unknown installer: ${installer}`);
  }
}


function isAlreadyInstalled(probeFn = probe) {
  return probeFn("mnemo");
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
