"use strict";

const readline = require("node:readline");


function promptScope({ stdin = process.stdin, stdout = process.stdout } = {}) {
  const rl = readline.createInterface({ input: stdin, output: stdout, terminal: false });
  const question = [
    "",
    "Where should mnemo install hooks?",
    "  [1] Global  — every Claude Code session (recommended)",
    "  [2] Project — only in this directory",
    "choice [1]: ",
  ].join("\n");
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      const trimmed = (answer || "").trim();
      if (trimmed === "2") return resolve("project");
      return resolve("global");
    });
  });
}


module.exports = { promptScope };
