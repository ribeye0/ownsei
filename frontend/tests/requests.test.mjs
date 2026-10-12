import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import test from "node:test";
import ts from "typescript";

async function moduleAt(name) {
  const source = readFileSync(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
}
const {api} = await moduleAt("api");
const {RequestLane} = await moduleAt("requestLane");

test("hard deadline settles even when the transport ignores abort", async () => {
  const fetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok:true, json:() => new Promise(() => {})});
  try {
    await assert.rejects(api("/stuck-body", "GET", undefined, {timeoutMs:10}), /請求逾時/);
  } finally {globalThis.fetch = fetch;}
});

test("stalled HTTP headers and JSON bodies time out; the next request still works", async () => {
  let bodyStarted = false;
  const server = createServer((req, res) => {
    if (req.url === "/api/headers") return;
    res.writeHead(200, {"Content-Type":"application/json"});
    if (req.url === "/api/body") {bodyStarted = true; res.write("["); return;}
    res.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const fetch = globalThis.fetch;
  globalThis.fetch = (path, options) => fetch(`http://127.0.0.1:${server.address().port}${path}`, options);
  try {
    assert.deepEqual(await api("/ok"), {ok:true});
    for (const path of ["/headers", "/body"]) {
      await assert.rejects(api(path, "GET", undefined, {timeoutMs:100}), /請求逾時/);
      assert.deepEqual(await api("/ok"), {ok:true});
    }
    assert.equal(bodyStarted, true);
    const controller = new AbortController();
    const request = api("/body", "GET", undefined, {signal:controller.signal});
    controller.abort();
    await assert.rejects(request, e => e.name === "AbortError");
  } finally {
    globalThis.fetch = fetch;
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test("a late old track response cannot overwrite the newer selection or its pending state", async () => {
  const pending = [], applied = [];
  const lane = new RequestLane(value => pending.push(value));
  let finishOld, finishNew, oldSignal;
  const old = lane.run(signal => {
    oldSignal = signal;
    return new Promise(resolve => {finishOld = resolve;});
  }, value => applied.push(value));
  const latest = lane.run(() => new Promise(resolve => {finishNew = resolve;}), value => applied.push(value));
  assert.equal(oldSignal.aborted, true);
  finishOld("old track"); await old;
  assert.deepEqual(applied, []);
  assert.equal(pending.at(-1), true);
  finishNew("new track"); await latest;
  assert.deepEqual(applied, ["new track"]);
  assert.equal(pending.at(-1), false);
});

test("failure releases pending controls, aborts sibling reads, and permits another switch", async () => {
  let pending, signal;
  const lane = new RequestLane(value => {pending = value;});
  await assert.rejects(lane.run(s => {signal = s; return Promise.reject(Error("timeout"));}, () => {}), /timeout/);
  assert.equal(pending, false);
  assert.equal(signal.aborted, true);
  let value;
  await lane.run(async () => "retry result", v => {value = v;});
  assert.equal(value, "retry result");
  assert.equal(pending, false);
});

test("cancelled project reads neither surface stale errors nor clear a newer read", async () => {
  let failOld, finishNew, pending;
  const lane = new RequestLane(value => {pending = value;});
  const old = lane.run(() => new Promise((_, reject) => {failOld = reject;}), () => assert.fail("obsolete"));
  lane.cancel();
  assert.equal(pending, false);
  const current = lane.run(() => new Promise(resolve => {finishNew = resolve;}), () => {});
  failOld(Error("old project failure")); await old;
  assert.equal(pending, true);
  finishNew("current"); await current;
  assert.equal(pending, false);
});
