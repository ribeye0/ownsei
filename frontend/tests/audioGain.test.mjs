import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/audioGain.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
const {MediaVolume} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function fixture() {
  const events = [];
  const media = {volume: 1, muted: false};
  const gain = {gain: {value: 1, setTargetAtTime: (...args) => events.push(["gain", ...args])},
    connect: node => events.push(["gain.connect", node]), disconnect: () => events.push(["gain.disconnect"])};
  const input = {connect: node => events.push(["source.connect", node]), disconnect: () => events.push(["source.disconnect"])};
  const context = {state: "suspended", currentTime: 3, destination: {},
    createGain: () => gain, createMediaElementSource: element => {events.push(["source", element]); return input;},
    resume: async () => {events.push(["resume"]); context.state = "running";},
    close: async () => {events.push(["close"]); context.state = "closed";}};
  const controller = new MediaVolume(media, () => {events.push(["context"]); return context;});
  return {events, media, gain, input, context, controller};
}

test("ordinary volume uses native audio; boost connects one source through gain to output", async () => {
  const {controller, media, gain, context, events} = fixture();
  controller.setLevel(0.5);
  assert.equal(media.volume, 0.5);
  await controller.resume();
  assert.equal(events.length, 0);
  controller.setLevel(2);
  await controller.resume();
  assert.equal(media.volume, 1);
  assert.equal(gain.gain.value, 2);
  assert.equal(events.find(e => e[0] === "source")[1], media);
  assert.equal(events.find(e => e[0] === "source.connect")[1], gain);
  assert.equal(events.find(e => e[0] === "gain.connect")[1], context.destination);
  assert.deepEqual(events.find(e => e[0] === "gain"), ["gain", 2, 3, 0.015]);
  assert.equal(context.state, "running");
  controller.setLevel(1.5);
  controller.setLevel(0.5);
  assert.equal(events.filter(e => e[0] === "context").length, 1);
  assert.equal(events.filter(e => e[0] === "source").length, 1);
  assert.equal(media.volume, 1); // No double attenuation after boost has been enabled.
  assert.deepEqual(events.at(-1), ["gain", 0.5, 3, 0.015]);
});

test("mute is preserved by gain changes; suspended contexts resume on later playback", async () => {
  const {controller, media, context, events} = fixture();
  controller.setLevel(1.75);
  await controller.resume();
  media.muted = true;
  controller.setLevel(1.75);
  assert.equal(media.muted, true);
  await controller.resume();
  assert.equal(events.filter(e => e[0] === "resume").length, 1);
  context.state = "suspended";
  await controller.resume();
  assert.equal(events.filter(e => e[0] === "resume").length, 2);
});

test("bounds stay safe and changing tracks releases both nodes and the context", () => {
  const {controller, media, events} = fixture();
  controller.setLevel(-1);
  assert.equal(media.volume, 0);
  controller.setLevel(NaN);
  assert.equal(media.volume, 1);
  controller.setLevel(9);
  assert.deepEqual(events.at(-1), ["gain", 2, 3, 0.015]);
  controller.dispose();
  controller.dispose();
  assert.deepEqual(events.slice(-3), [["source.disconnect"], ["gain.disconnect"], ["close"]]);
  assert.equal(events.filter(e => e[0] === "close").length, 1);
});

test("failure to create a gain context leaves native playback usable", () => {
  const media = {volume: 0.8};
  const controller = new MediaVolume(media, () => {throw new Error("not supported");});
  assert.throws(() => controller.setLevel(2), /not supported/);
  assert.equal(media.volume, 0.8);
  controller.setLevel(0.5);
  assert.equal(media.volume, 0.5);
  controller.dispose();
});
