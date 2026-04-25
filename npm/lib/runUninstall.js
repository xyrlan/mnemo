"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const { pickInstaller } = require("./detect");
const { runShell } = require("./bootstrap");
const { buildUninstallArgs, runMnemo } = require("./runMnemo");
const m = require("./messages");


function parseUninstallFlags(argv) {
  const f = { scope: null, yes: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--scope") f.scope = argv[++i];
    else if (a === "--yes" || a === "-y") f.yes = true;
    else if (a === "--quiet") f.quiet = true;
  }
  return f;
}


function _hasMnemoInSettings(settingsPath) {
  if (!fs.existsSync(settingsPath)) return false;
  try {
    const data = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    const hooks = (data && data.hooks) || {};
    for (const ev of Object.keys(hooks)) {
      const entries = hooks[ev] || [];
      for (const e of entries) {
        for (const h of (e.hooks || [])) {
          if (typeof h.command === "string" && h.command.includes("mnemo.hooks.")) return true;
        }
      }
    }
    return false;
  } catch (_e) {
    return false;
  }
}


function detectScopes() {
  return {
    project: _hasMnemoInSettings(path.join(process.cwd(), ".claude", "settings.json")),
    global:  _hasMnemoInSettings(path.join(os.homedir(), ".claude", "settings.json")),
  };
}


function resolveUninstallScope(flag, present, nonInteractive) {
  if (flag) {
    if (!["project", "global", "both"].includes(flag)) {
      throw new Error(`invalid --scope: ${flag}`);
    }
    return flag;
  }
  const both = present.project && present.global;
  if (both && nonInteractive) {
    throw new Error("both project and global installs detected; pass --scope explicitly");
  }
  if (present.project && !present.global) return "project";
  if (present.global && !present.project) return "global";
  return null;
}


async function _promptUninstallScope() {
  const readline = require("node:readline");
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const question = [
    "",
    "Both project and global mnemo installs detected.",
    "  [1] project (this directory)",
    "  [2] global",
    "  [3] both",
    "choice [1]: ",
  ].join("\n");
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      const t = (answer || "").trim();
      if (t === "2") return resolve("global");
      if (t === "3") return resolve("both");
      return resolve("project");
    });
  });
}


async function runUninstall(argv) {
  const flags = parseUninstallFlags(argv);
  const present = detectScopes();
  if (!present.project && !present.global) {
    m.warn("No mnemo install detected (no hooks in project or global settings).");
    return 0;
  }
  let scope;
  try {
    scope = resolveUninstallScope(flags.scope, present, flags.yes) || await _promptUninstallScope();
  } catch (e) {
    m.err(e.message);
    return 2;
  }

  const scopes = scope === "both" ? ["project", "global"] : [scope];
  for (const s of scopes) {
    const status = runMnemo(buildUninstallArgs({ scope: s, quiet: flags.quiet, yes: true }), { quiet: flags.quiet });
    if (status !== 0) return status;
  }

  const installer = pickInstaller();
  if (installer) {
    const cmd = installer === "uv" ? "uv tool uninstall mnemo-claude"
              : installer === "pipx" ? "pipx uninstall mnemo-claude"
              : "python3 -m pip uninstall -y --user mnemo-claude";
    if (!flags.quiet) m.info(`Running: ${cmd}`);
    runShell(cmd, { quiet: flags.quiet });
  }
  if (!flags.quiet) m.plain("\nDone. Vault preserved.");
  return 0;
}


module.exports = { runUninstall, parseUninstallFlags, resolveUninstallScope, detectScopes };
