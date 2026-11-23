import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const output = ts.transpileModule(readFileSync(new URL("../src/transcriptFollow.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { followCue, followScrollTop } = await import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
const cues = [
  {id:"a",start_ms:1000,end_ms:2000},
  {id:"b",start_ms:3000,end_ms:4000},
  {id:"c",start_ms:5000,end_ms:6000},
];

test("following handles forward and backward seeks and keeps the previous cue in silent gaps",()=>{
  assert.equal(followCue(cues, 1), "a");
  assert.equal(followCue(cues, 5.5), "c");
  assert.equal(followCue(cues, 3.5), "b");
  assert.equal(followCue(cues, 2), "a");
  assert.equal(followCue(cues, 4.9), "b");
  assert.equal(followCue(cues, 0), "a");
  assert.equal(followCue(cues, 100), "c");
  assert.equal(followCue([], 2), undefined);
  assert.equal(followCue(cues, NaN), undefined);
});
test("overlapping or out-of-order timing chooses the most recently started active line",()=>{
  const overlap = [{id:"long",start_ms:0,end_ms:10000}, ...cues];
  assert.equal(followCue(overlap, 3.5), "b");
  assert.equal(followCue([...cues].reverse(), 3.5), "b");
  assert.equal(followCue(overlap, 4.5), "long");
});
test("only offscreen rows scroll; a visible or oversized row never causes repeated jumps",()=>{
  assert.equal(followScrollTop(100, 300, 25, 80), undefined);
  assert.equal(followScrollTop(100, 300, 290, 80), 280);
  assert.equal(followScrollTop(100, 300, -100, 80), 0);
  assert.equal(followScrollTop(100, 300, -40, 500), undefined);
  assert.equal(followScrollTop(100, 0, 290, 80), undefined);
  assert.equal(followScrollTop(100, 300, NaN, 80), undefined);
});
