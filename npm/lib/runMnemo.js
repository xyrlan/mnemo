"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");


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


// Resolve the real Python `mnemo` entry point, skipping the npx-injected
// wrapper bin so spawnSync doesn't recurse into us.
// `selfBinDir` defaults to the directory of the currently executing
// wrapper (process.argv[1]); override for testing.
function resolveMnemoBinary({ env = process.env, platform = process.platform, selfBinDir } = {}) {
  const exeName = platform === "win32" ? "mnemo.exe" : "mnemo";
  const sep = platform === "win32" ? ";" : ":";
  const rawPath = (env.PATH || env.Path || "").split(sep);
  const skip = new Set();
  if (selfBinDir) skip.add(path.resolve(selfBinDir));
  return rawPath
    .filter((dir) => dir && !skip.has(path.resolve(dir)) && !/[\\/]_npx[\\/]/.test(dir))
    .map((dir) => path.join(dir, exeName))
    .find((candidate) => {
      try { return fs.statSync(candidate).isFile(); }
      catch (_e) { return false; }
    }) || null;
}


function runMnemo(args, { quiet = false, selfBinDir } = {}) {
  const bin = resolveMnemoBinary({ selfBinDir: selfBinDir || (process.argv[1] && path.dirname(process.argv[1])) });
  const target = bin || "mnemo";
  const result = spawnSync(target, args, { stdio: quiet ? "ignore" : "inherit" });
  return result.status === null ? 1 : result.status;
}


module.exports = { buildInitArgs, buildUninstallArgs, runMnemo, resolveMnemoBinary };
