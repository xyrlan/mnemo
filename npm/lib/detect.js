"use strict";

const { execSync } = require("node:child_process");


function parsePythonVersion(stdout) {
  const m = /Python\s+(\d+)\.(\d+)(?:\.(\d+))?/.exec(stdout || "");
  if (!m) return null;
  return {
    major: parseInt(m[1], 10),
    minor: parseInt(m[2], 10),
    patch: m[3] ? parseInt(m[3], 10) : 0,
  };
}


function probe(cmd) {
  try {
    execSync(`${cmd} --version`, { stdio: "ignore" });
    return true;
  } catch (_e) {
    return false;
  }
}


function pickInstaller(probeFn = probe) {
  if (probeFn("uv")) return "uv";
  if (probeFn("pipx")) return "pipx";
  if (probeFn("pip") || probeFn("pip3") || probeFn("python3 -m pip")) return "pip-user";
  return null;
}


function detectPython() {
  for (const cmd of ["python3", "python"]) {
    try {
      const out = execSync(`${cmd} --version`, { encoding: "utf8" });
      const v = parsePythonVersion(out);
      if (v && (v.major > 3 || (v.major === 3 && v.minor >= 8))) {
        return { version: v, command: cmd };
      }
    } catch (_e) {
      continue;
    }
  }
  return null;
}


function isPep668(stderr) {
  return /externally-managed-environment/i.test(stderr || "");
}


function pep668InstallHint() {
  if (process.platform === "darwin") return "brew install pipx";
  try {
    const osr = require("node:fs").readFileSync("/etc/os-release", "utf8");
    if (/ID=(?:debian|ubuntu)/i.test(osr)) return "sudo apt install pipx";
    if (/ID=(?:fedora|rhel|centos)/i.test(osr)) return "sudo dnf install pipx";
  } catch (_e) { /* ignore */ }
  return "install pipx via your system package manager";
}


module.exports = { parsePythonVersion, probe, pickInstaller, detectPython, isPep668, pep668InstallHint };
