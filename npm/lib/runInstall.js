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


function parseFlags(argv, { warn = (msg) => process.stderr.write(`warning: ${msg}\n`) } = {}) {
  const flags = { scope: null, vaultRoot: null, upgrade: false, yes: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--global")              flags.scope = "global";
    else if (a === "--project" || a === "--local") flags.scope = "project";
    else if (a === "--upgrade")        flags.upgrade = true;
    else if (a === "--yes" || a === "-y") flags.yes = true;
    else if (a === "--quiet")          flags.quiet = true;
    else if (a === "--vault-root") {
      const next = argv[i + 1];
      if (!next || next.startsWith("--")) {
        warn(`--vault-root requires a path argument; ignoring.`);
      } else {
        flags.vaultRoot = next;
        i++;
      }
    }
    else if (a && a.startsWith("-")) {
      warn(`unknown flag: ${a}`);
    }
  }
  return flags;
}


// Decide what we need before installing anything.
//
// Installer first, Python second -- the reverse order bailed out on a machine
// that had uv but no system Python, which is precisely the case uv solves: it
// provisions its own CPython. The tool that would have worked was never
// reached, and the user was told to go install Python.
function resolveToolchain({ pickInstallerFn = pickInstaller, detectPythonFn = detectPython } = {}) {
  const installer = pickInstallerFn();
  const python = detectPythonFn();

  if (installer === "uv") return { installer, python };

  if (!installer) {
    return {
      error: python
        ? `No Python installer (uv, pipx, or pip) found on PATH.\n  → ${pep668InstallHint()}`
        : "Neither a Python installer nor Python 3.8+ was found.\n" +
          "  → Install uv (https://docs.astral.sh/uv/) — it brings its own Python:\n" +
          "      curl -LsSf https://astral.sh/uv/install.sh | sh",
    };
  }

  // pipx and pip both run on the system interpreter.
  if (!python) {
    return {
      error:
        `Python 3.8+ not found (required by ${installer}).\n` +
        "  → Install Python 3.8+ (https://www.python.org/downloads/), or install uv,\n" +
        "    which brings its own: curl -LsSf https://astral.sh/uv/install.sh | sh",
    };
  }

  return { installer, python };
}


async function runInstall(argv) {
  const flags = parseFlags(argv);

  const { installer, python: py, error } = resolveToolchain();
  if (error) {
    const [first, ...rest] = error.split("\n");
    m.err(first);
    rest.forEach((line) => m.plain(line));
    return 1;
  }
  if (!flags.quiet) {
    if (py) m.ok(`Python ${py.version.major}.${py.version.minor} detected`);
    m.ok(`installer: ${installer}`);
  }

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


module.exports = { runInstall, parseFlags, resolveToolchain };
