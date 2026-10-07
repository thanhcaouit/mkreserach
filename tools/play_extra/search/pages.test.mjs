import assert from "node:assert/strict";
import test from "node:test";

import { collectSearchApps } from "./pages.mjs";

function gridApp(id) {
  const item = [];
  item[0] = [];
  item[0][0] = [id];
  item[0][3] = `Title ${id}`;
  item[0][14] = "Dev";
  return item;
}

function pageApp(id) {
  const item = [];
  item[0] = [id];
  item[3] = `Title ${id}`;
  item[14] = "Dev";
  return item;
}

function section(apps, token) {
  const block = [];
  block[22] = [apps];
  if (token) {
    block[22][1] = [];
    block[22][1][3] = [];
    block[22][1][3][1] = token;
  }
  return block;
}

test("keeps a later section and the page behind its token", async () => {
  const calls = [];
  const rows = await collectSearchApps(
    [section([gridApp("first.id")], "tok-1"), section([gridApp("second.id")], "tok-2")],
    async (token) => {
      calls.push(token);
      if (token === "tok-2") return { apps: [pageApp("second.page")], token: "" };
      return { apps: [pageApp("first.page")], token: "" };
    },
  );
  assert.deepEqual(
    rows.map((row) => row.appId),
    ["first.id", "first.page", "second.id", "second.page"],
  );
  assert.deepEqual(calls, ["tok-1", "tok-2"]);
});

test("does not continue a section that has no token", async () => {
  const calls = [];
  const rows = await collectSearchApps([section([gridApp("only.id")])], async (token) => {
    calls.push(token);
    return { apps: [pageApp("should.not")], token: "" };
  });
  assert.deepEqual(calls, []);
  assert.deepEqual(
    rows.map((row) => row.appId),
    ["only.id"],
  );
});
