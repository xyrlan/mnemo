"use strict";

const C = {
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  red:    "\x1b[31m",
  dim:    "\x1b[2m",
  reset:  "\x1b[0m",
};

function ok(msg)    { process.stdout.write(`${C.green}✓${C.reset} ${msg}\n`); }
function warn(msg)  { process.stdout.write(`${C.yellow}⚠${C.reset} ${msg}\n`); }
function err(msg)   { process.stderr.write(`${C.red}✗${C.reset} ${msg}\n`); }
function info(msg)  { process.stdout.write(`${C.dim}↻${C.reset} ${msg}\n`); }
function plain(msg) { process.stdout.write(`${msg}\n`); }

module.exports = { ok, warn, err, info, plain };
