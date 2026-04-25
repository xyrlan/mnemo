"use strict";

const { spawnSync } = require("node:child_process");


function buildInitArgs({ scope, vaultRoot, quiet, yes = true }) {
  const args = ["init"];
  if (scope === "project") args.push("--project");
  if (yes) args.push("--yes");
  if (quiet) args.push("--quiet");
  if (vaultRoot) { args.push("--vault-root", vaultRoot); }
  return args;
}


function buildUninstallArgs({ scope, quiet, yes = true }) {
  const args = ["uninstall"];
  if (scope === "project") args.push("--project");
  if (yes) args.push("--yes");
  if (quiet) args.push("--quiet");
  return args;
}


function runMnemo(args, { quiet = false } = {}) {
  const result = spawnSync("mnemo", args, { stdio: quiet ? "ignore" : "inherit" });
  return result.status === null ? 1 : result.status;
}


module.exports = { buildInitArgs, buildUninstallArgs, runMnemo };
