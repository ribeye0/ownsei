import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/audioTransport.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
const {AudioTransport} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const flush = async () => {await Promise.resolve(); await Promise.resolve();};

function fixture(t) {
  t.mock.timers.enable({apis: ["setInterval"]});
  const calls = [], states = [], errors = [];
  let clock = 0;
  class Media extends EventTarget {
    paused = true;
    ended = false;
    seeking = false;
    readyState = 4;
    preload = "metadata";
    removeAttribute(name) {calls.push(["removeAttribute",name]);}
    position = 12;
    get currentTime() {return this.position;}
    set currentTime(value) {this.position = value; this.seeking = true; calls.push(["seek",value]); this.dispatchEvent(new Event("seeking"));}
    play() {
      calls.push(["play"]);
      if (this.paused) {this.paused = false; this.dispatchEvent(new Event("play"));}
      this.dispatchEvent(new Event("playing"));
      return Promise.resolve();
    }
    pause() {
      if (this.paused) return;
      calls.push(["pause"]); this.paused = true;
      queueMicrotask(() => this.dispatchEvent(new Event("pause")));
    }
    load() {calls.push(["load"]); this.paused = true;}
    completeSeek(position = this.position) {
      this.position = position; this.seeking = false; this.dispatchEvent(new Event("seeked"));
    }
  }
  const media = new Media();
  const transport = new AudioTransport(media, async () => {calls.push(["resume"]);}, state => states.push(state), message => errors.push(message), () => clock);
  t.after(() => transport.dispose());
  return {media, transport, calls, states, errors, elapse: ms => {while (ms > 0) {const step = Math.min(250, ms); clock += step; t.mock.timers.tick(step); ms -= step;}}};
}

test("playing seeks keep the native decoder active, including gain", async t => {
  const {media, transport, calls, states} = fixture(t);
  transport.play(); await flush();
  transport.seek(120); await flush();
  assert.equal(media.paused, false);
  assert.equal(calls.filter(c => c[0] === "pause").length, 0);
  assert.equal(states.at(-1), "seeking");
  media.completeSeek(); await flush();
  assert.equal(media.paused, false);
  assert.equal(media.currentTime, 120);
  assert.equal(states.at(-1), "playing");
  assert.equal(calls.filter(c => c[0] === "play").length, 3);
  assert.ok(calls.filter(c => c[0] === "resume").length >= 2);
});

test("rapid seeks keep playing intent and ignore completion of an older target", async t => {
  const {media, transport, calls} = fixture(t);
  transport.play(); await flush();
  transport.seek(100); transport.seek(200); await flush();
  media.dispatchEvent(new Event("seeked")); await flush();
  assert.equal(media.seeking, true);
  assert.equal(media.paused, false);
  media.completeSeek(200); await flush();
  assert.equal(media.paused, false);
  assert.equal(calls.filter(c => c[0] === "play").length, 4);
});

test("paused scrubbing stays paused; user pause during a seek cancels automatic continuation", async t => {
  const {media, transport, calls, states} = fixture(t);
  transport.seek(120); media.completeSeek(); await flush();
  assert.equal(media.paused, true);
  assert.equal(calls.filter(c => c[0] === "play").length, 0);
  transport.play(); await flush();
  transport.seek(240); transport.toggle(); await flush();
  media.completeSeek(); await flush();
  assert.equal(media.paused, true);
  assert.equal(states.at(-1), "paused");
  assert.equal(calls.filter(c => c[0] === "play").length, 2);
});

test("a stuck decoder reloads once, preserves target and resumes after metadata and seek", async t => {
  const {media, transport, calls, elapse, states, errors} = fixture(t);
  transport.play(); await flush();
  transport.seek(240); await flush();
  elapse(8000); await flush();
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  assert.equal(states.at(-1), "buffering");
  media.position = 0;
  media.dispatchEvent(new Event("loadedmetadata"));
  assert.equal(media.currentTime, 240);
  media.completeSeek(); await flush();
  assert.equal(media.paused, false);
  assert.equal(states.at(-1), "playing");
  assert.deepEqual(errors, []);
});

test("user pause cancels recovery even if a seek never completes", async t => {
  const {transport, calls, elapse, states} = fixture(t);
  transport.play(); await flush(); transport.seek(240); transport.pause(); await flush();
  elapse(24000);
  assert.equal(calls.filter(c => c[0] === "load").length, 0);
  assert.equal(states.at(-1), "paused");
});

test("persistent stalls stop with an error; retry restores the position without unbounded reloads", async t => {
  const {media, transport, calls, elapse, states, errors} = fixture(t);
  transport.play(); await flush();
  elapse(8000); await flush(); elapse(8000); await flush();
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  assert.equal(states.at(-1), "paused");
  assert.match(errors[0], /重試播放/);
  elapse(24000);
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  transport.retry();
  media.dispatchEvent(new Event("loadedmetadata")); media.completeSeek(); await flush();
  assert.equal(media.currentTime, 12);
  assert.equal(media.paused, false);
});

test("real position advances prevent recovery; native external play and pause remain supported", async t => {
  const {media, calls, elapse, states} = fixture(t);
  await media.play();
  for (let i=0;i<32;i++) {media.position += .5; media.dispatchEvent(new Event("timeupdate")); elapse(500);}
  assert.equal(calls.filter(c => c[0] === "load").length, 0);
  media.dispatchEvent(new Event("waiting")); assert.equal(states.at(-1), "buffering");
  media.dispatchEvent(new Event("playing")); assert.equal(states.at(-1), "playing");
  media.pause(); await flush(); assert.equal(states.at(-1), "paused");
  elapse(16000); assert.equal(calls.filter(c => c[0] === "load").length, 0);
});

test("a stale rejected play request cannot stop a newer seek, and disposal cancels timers", async t => {
  const {media, transport, calls, elapse, errors} = fixture(t);
  const rejects = [];
  media.play = () => {media.paused = false; return new Promise((_, r) => {rejects.push(r);});};
  transport.play(); transport.seek(140);
  rejects[0](new Error("old request")); await flush();
  assert.deepEqual(errors, []);
  transport.dispose(); await flush();
  const count = calls.length;
  elapse(30000); media.dispatchEvent(new Event("canplay"));
  assert.equal(calls.length, count);
});


test("play requests target data immediately even when a paused decoder defers seeked", async t => {
  const {media, transport, states} = fixture(t);
  transport.seek(100);
  assert.equal(media.paused, true);
  transport.play(); await flush();
  assert.equal(media.paused, false);
  media.completeSeek(99.85); await flush();
  assert.equal(states.at(-1), "playing");
});

test("resolved play without another playing event clears buffering; stale resolution preserves pause", async t => {
  const {media, transport, states} = fixture(t);
  media.paused = false;
  media.play = () => Promise.resolve();
  transport.play(); await flush();
  assert.equal(states.at(-1), "playing");
  media.dispatchEvent(new Event("stalled"));
  media.position += 1; media.dispatchEvent(new Event("timeupdate"));
  assert.equal(states.at(-1), "playing");
  let resolve;
  media.play = () => new Promise(r => {resolve = r;});
  transport.play(); transport.pause(); resolve(); await flush();
  assert.equal(states.at(-1), "paused");
});


test("first play promotes metadata loading and a pending startup recovers at the exact same position", async t => {
  const {media, transport, calls, elapse} = fixture(t);
  media.position = 0; media.readyState = 1;
  const requests = [];
  media.play = () => {
    media.paused = false;
    return new Promise(resolve => requests.push(resolve));
  };
  transport.play();
  assert.equal(media.preload, "auto");
  elapse(1250); assert.equal(calls.filter(c => c[0] === "seek").length, 0);
  elapse(250);
  assert.deepEqual(calls.filter(c => c[0] === "seek"), [["seek", 0]]);
  assert.equal(media.paused, false);
  media.readyState = 4; media.completeSeek(); requests.forEach(resolve => resolve()); await flush();
  media.position = .5; media.dispatchEvent(new Event("timeupdate"));
  elapse(500);
  assert.equal(calls.filter(c => c[0] === "load").length, 0);
});

test("startup nudge is bounded and pause cancels it; slow metadata waits without an invalid seek", async t => {
  const {media, transport, calls, elapse} = fixture(t);
  media.position = 0; media.readyState = 0;
  transport.play(); await flush();
  elapse(1750); assert.equal(calls.filter(c => c[0] === "seek").length, 0);
  media.readyState = 1;
  elapse(250); assert.equal(calls.filter(c => c[0] === "seek").length, 1);
  media.completeSeek(); await flush();
  elapse(2000); assert.equal(calls.filter(c => c[0] === "seek").length, 1);
  transport.pause(); await flush(); elapse(20000);
  assert.equal(calls.filter(c => c[0] === "load").length, 0);
});

test("healthy playback is never nudged, and disposal aborts the old source exactly once", async t => {
  const {media, transport, calls, elapse, errors} = fixture(t);
  transport.play(); await flush();
  for (let i=0;i<10;i++) {media.position += .5; media.dispatchEvent(new Event("timeupdate")); elapse(500);}
  assert.equal(calls.filter(c => c[0] === "seek").length, 0);
  transport.dispose(); transport.dispose();
  assert.deepEqual(calls.slice(-3), [["pause"], ["removeAttribute", "src"], ["load"]]);
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  const count = calls.length;
  transport.play(); transport.seek(200); transport.retry(); transport.pause();
  elapse(30000); await flush();
  assert.equal(calls.length, count); assert.deepEqual(errors, []);
});


test("repeated ready/playing events without real progress do not postpone the startup watchdog", async t => {
  const {media, transport, calls, elapse, states} = fixture(t);
  transport.play(); await flush();
  for (let i=0;i<32;i++) {media.dispatchEvent(new Event("playing")); elapse(250);}
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  assert.equal(states.at(-1), "buffering");
});


test("reported ready=0 startup stall never seeks before metadata and reload remains bounded", async t => {
  const {media, transport, calls, elapse} = fixture(t);
  media.position = 0; media.readyState = 0;
  media.play = () => {media.paused = false; return new Promise(() => {});};
  transport.play();
  media.dispatchEvent(new Event("waiting")); elapse(2000);
  media.dispatchEvent(new Event("stalled")); elapse(6000);
  assert.equal(calls.filter(c => c[0] === "seek").length, 0);
  assert.equal(calls.filter(c => c[0] === "load").length, 1);
  media.readyState = 1; media.dispatchEvent(new Event("loadedmetadata"));
  assert.deepEqual(calls.filter(c => c[0] === "seek"), [["seek", 0]]);
});

test("disposing an old pending play cannot pause or report an error on the next track", async t => {
  const {media: old, transport, calls, errors} = fixture(t);
  let reject;
  old.play = () => {old.paused = false; return new Promise((_, r) => {reject = r;});};
  transport.play(); transport.dispose();
  const next = new old.constructor(); next.position = 0;
  const states = [];
  const fresh = new AudioTransport(next, async () => {}, state => states.push(state), error => errors.push(error));
  t.after(() => fresh.dispose());
  fresh.play(); await flush(); reject(new Error("old source aborted")); await flush();
  assert.equal(next.paused, false); assert.equal(next.preload, "auto");
  assert.equal(states.at(-1), "playing"); assert.deepEqual(errors, []);
  assert.ok(calls.some(c => c[0] === "removeAttribute" && c[1] === "src"));
});
