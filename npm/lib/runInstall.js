"use strict";

const { detectPython, pickInstaller, pep668InstallHint } = require("./detect");
const {
  PIN_SPEC,
  buildInstallCmd,
  buildUpgradeCmd,
  isAlreadyInstalled,
  runShell,
  verifyOnPath,
  pathFixHint,
} = require("./bootstrap");
const { promptScope } = require("./prompt");
const { buildInitArgs, runMnemo } = require("./runMnemo");
const m = require("./messages");


function parseFlags(argv) {
  const flags = { scope: null, vaultRoot: null, upgrade: false, yes: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--global")              flags.scope = "global";
    else if (a === "--project" || a === "--local") flags.scope = "project";
    else if (a === "--upgrade")        flags.upgrade = true;
    else if (a === "--yes" || a === "-y") flags.yes = true;
    else if (a === "--quiet")          flags.quiet = true;
    else if (a === "--vault-root")     { flags.vaultRoot = argv[++i]; }
  }
  return flags;
}


async function runInstall(argv) {
  const flags = parseFlags(argv);

  const py = detectPython();
  if (!py) {
    m.err("Python 3.8+ not found.");
    m.plain("  → Install Python 3.8 or newer (https://www.python.org/downloads/) and retry.");
    return 1;
  }
  if (!flags.quiet) m.ok(`Python ${py.version.major}.${py.version.minor} detected`);

  const installer = pickInstaller();
  if (!installer) {
    m.err("No Python installer (uv, pipx, or pip) found on PATH.");
    m.plain(`  → ${pep668InstallHint()}`);
    return 1;
  }
  if (!flags.quiet) m.ok(`installer: ${installer}`);

  const installed = isAlreadyInstalled();
  if (installed && !flags.upgrade) {
    if (!flags.quiet) m.ok("mnemo already installed. Skipping installer step. (use --upgrade to force)");
  } else {
    const cmd = installed ? buildUpgradeCmd(installer) : buildInstallCmd(installer, PIN_SPEC);
    if (!flags.quiet) m.info(`Running: ${cmd}`);
    const status = runShell(cmd, { quiet: flags.quiet });
    if (status !== 0) {
      m.err(`Installer command failed (exit ${status}).`);
      return status;
    }
    if (!verifyOnPath()) {
      m.err("`mnemo --version` not reachable on PATH after install.");
      m.plain(`  → ${pathFixHint(installer)}`);
      return 2;
    }
    if (!flags.quiet) m.ok("mnemo on PATH");
  }

  let scope = flags.scope;
  if (!scope) {
    if (flags.yes) scope = "global";
    else scope = await promptScope();
  }

  const args = buildInitArgs({ scope, vaultRoot: flags.vaultRoot, quiet: flags.quiet, yes: true });
  const status = runMnemo(args, { quiet: flags.quiet });
  if (status !== 0) return status;

  if (!flags.quiet) {
    if (scope === "project") {
      m.plain(`\nDone. Launch \`claude\` in ${process.cwd()} to activate the local hooks.`);
    } else {
      m.plain("\nDone. Open Claude Code anywhere; mnemo is active.");
    }
  }
  return 0;
}


module.exports = { runInstall, parseFlags };
