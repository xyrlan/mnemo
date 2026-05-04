"use strict";

const { execSync, spawnSync } = require("node:child_process");
const { probeOnPath } = require("./detect");
const { resolveMnemoBinary } = require("./runMnemo");


const PIN_SPEC = "mnemo-claude>=0.15,<0.16";


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


// Detect a real mnemo install. We can NOT trust `command -v mnemo` here:
// when running under `npx @xyrlan/mnemo`, npm injects the wrapper itself
// onto PATH, so `command -v mnemo` resolves to us. resolveMnemoBinary
// filters npx temp dirs so it only matches a real Python install.
function isAlreadyInstalled(resolverFn = resolveMnemoBinary) {
  const selfBinDir = process.argv[1] ? require("node:path").dirname(process.argv[1]) : null;
  return Boolean(resolverFn({ selfBinDir }));
}


function runShell(cmd, { quiet = false, platform = process.platform } = {}) {
  const isWin = platform === "win32";
  const shellCmd = isWin ? "cmd.exe" : "sh";
  const shellArgs = isWin ? ["/d", "/s", "/c", cmd] : ["-c", cmd];
  const result = spawnSync(shellCmd, shellArgs, {
    stdio: quiet ? "ignore" : "inherit",
    windowsHide: true,
  });
  return result.status === null ? 1 : result.status;
}


function verifyOnPath() {
  const { resolveMnemoBinary } = require("./runMnemo");
  const path = require("node:path");
  const selfBinDir = process.argv[1] ? path.dirname(process.argv[1]) : null;
  return Boolean(resolveMnemoBinary({ selfBinDir }));
}


function _userBaseFromPython(execFn) {
  // `python3 -m site --user-base` is authoritative across platforms:
  //   Linux  → ~/.local
  //   macOS  → ~/Library/Python/<X.Y>     (NOT ~/.local — common gotcha)
  //   Win    → %APPDATA%\Python
  // Returns the bin/Scripts dir, or null on failure (caller falls back to
  // platform heuristics).
  try {
    const out = execFn("python3 -m site --user-base").trim();
    if (!out) return null;
    return out;
  } catch (_e) {
    return null;
  }
}


function pathFixHint(installer, {
  platform = process.platform,
  execFn = (cmd) => execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }),
} = {}) {
  if (installer === "pipx") return "Run `pipx ensurepath` and reopen your shell.";
  if (installer === "uv")   return "Run `uv tool update-shell` and reopen your shell.";
  if (installer === "pip-user") {
    if (platform === "win32") {
      const base = _userBaseFromPython(execFn);
      if (base) return `Add ${base}\\Scripts to PATH.`;
      return "Add %APPDATA%\\Python\\Scripts to PATH.";
    }
    const base = _userBaseFromPython(execFn);
    if (base) return `Add ${base}/bin to PATH (e.g. via your shell profile).`;
    if (platform === "darwin") {
      return "Add ~/Library/Python/<X.Y>/bin to PATH (run `python3 -m site --user-base` to find the right X.Y).";
    }
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
