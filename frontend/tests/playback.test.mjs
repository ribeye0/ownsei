import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Load the same TS source used by Vite without adding a browser test runtime.
const source = readFileSync(new URL("../src/playback.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
const {currentSubtitles, playbackTime, seekPosition} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const cue = (id, start_ms, end_ms, ja, zh) => ({id, ordinal: 0, start_ms, end_ms, ja, zh});

test("subtitles switch at exact start/end boundaries and clear in silent gaps", () => {
  const cues = [cue("a", 1000, 2000, "こんにちは", "你好"), cue("b", 3000, 4000, "またね", "再見")];
  assert.deepEqual(currentSubtitles(cues, 0.999), {ids: [], ja: "", zh: ""});
  assert.deepEqual(currentSubtitles(cues, 1), {ids: ["a"], ja: "こんにちは", zh: "你好"});
  assert.deepEqual(currentSubtitles(cues, 2), {ids: [], ja: "", zh: ""});
  assert.deepEqual(currentSubtitles(cues, 3), {ids: ["b"], ja: "またね", zh: "再見"});
  assert.deepEqual(currentSubtitles(cues, 4), {ids: [], ja: "", zh: ""});
});

test("either missing language stays empty without reusing the previous cue", () => {
  const cues = [cue("ja", 0, 1000, "原文", null), cue("zh", 1000, 2000, "", "人工譯文")];
  assert.deepEqual(currentSubtitles(cues, 0.5), {ids: ["ja"], ja: "原文", zh: ""});
  assert.deepEqual(currentSubtitles(cues, 1.5), {ids: ["zh"], ja: "", zh: "人工譯文"});
  assert.deepEqual(currentSubtitles([], 1), {ids: [], ja: "", zh: ""});
});

test("overlapping cues retain both lines; replacing the selected version updates text", () => {
  const cues = [cue("a", 0, 2000, "一", "甲"), cue("b", 1000, 3000, "二", "乙")];
  assert.deepEqual(currentSubtitles(cues, 1.5), {ids: ["a", "b"], ja: "一\n二", zh: "甲\n乙"});
  const revised = [{...cues[0], ja: "校對原文", zh: "另一模型譯文"}];
  assert.deepEqual(currentSubtitles(revised, 1.5), {ids: ["a"], ja: "校對原文", zh: "另一模型譯文"});
  assert.equal(cues[0].ja, "一");
});

test("seeking clamps at both audio boundaries including unloaded/invalid durations", () => {
  assert.equal(seekPosition(-10, 30), 0);
  assert.equal(seekPosition(50, 30), 30);
  assert.equal(seekPosition(4.5, 30), 4.5);
  assert.equal(seekPosition(4.5, 0), 0);
  assert.equal(seekPosition(Infinity, 30), 0);
  assert.equal(seekPosition(10, NaN), 0);
});

test("long recordings show hours and invalid positions are safe to display", () => {
  assert.equal(playbackTime(383.44), "06:23");
  assert.equal(playbackTime(3661.5), "1:01:01");
  assert.equal(playbackTime(0), "00:00");
  assert.equal(playbackTime(-1), "00:00");
  assert.equal(playbackTime(NaN), "00:00");
});
