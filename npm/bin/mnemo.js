#!/usr/bin/env node
"use strict";

const { runInstall } = require("../lib/runInstall");
const { runUninstall } = require("../lib/runUninstall");

function printHelp() {
  process.stdout.write([
    "Usage: npx @xyrlan/mnemo <command> [flags]",
    "",
    "Commands:",
    "  install      Install mnemo (Python) + register hooks/slash commands",
    "  uninstall    Remove mnemo from this scope (vault preserved)",
    "  help         Show this message",
    "",
    "Install flags:",
    "  --global             Install globally (default)",
    "  --project, --local   Install only in the current directory",
    "  --vault-root <path>  Override vault location",
    "  --upgrade            Force upgrade if already installed",
    "  --yes, -y            Non-interactive (default: global)",
    "  --quiet              Suppress informational output",
    "",
    "Uninstall flags:",
    "  --scope global|project|both",
    "  --yes, -y",
    "  --quiet",
    "",
  ].join("\n"));
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  const rest = argv.slice(1);
  switch (cmd) {
    case "install":
      process.exit(await runInstall(rest));
    case "uninstall":
      process.exit(await runUninstall(rest));
    case undefined:
    case "help":
    case "--help":
    case "-h":
      printHelp();
      process.exit(0);
    default:
      process.stderr.write(`unknown command: ${cmd}\n`);
      printHelp();
      process.exit(2);
  }
}

main().catch((err) => {
  process.stderr.write(`fatal: ${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
