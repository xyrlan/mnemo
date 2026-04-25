"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { Readable, Writable } = require("node:stream");

const { promptScope } = require("../lib/prompt");

function fakeStreams(input) {
  const stdin = Readable.from([input]);
  const chunks = [];
  const stdout = new Writable({
    write(chunk, _enc, cb) { chunks.push(chunk.toString()); cb(); },
  });
  return { stdin, stdout, chunks };
}

test("promptScope defaults to global on empty input", async () => {
  const { stdin, stdout } = fakeStreams("\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "global");
});

test("promptScope returns project on '2'", async () => {
  const { stdin, stdout } = fakeStreams("2\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "project");
});

test("promptScope returns global on '1'", async () => {
  const { stdin, stdout } = fakeStreams("1\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "global");
});
