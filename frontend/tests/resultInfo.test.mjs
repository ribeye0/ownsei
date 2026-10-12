import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/resultInfo.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
const {resultStamp, issueReport} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("result labels use short local dates and disclose unknown or estimated times", () => {
  const stamp = new Date(2026, 9, 11, 12, 34, 56).getTime() / 1000;
  assert.equal(resultStamp(stamp), "26-10-11 12:34:56");
  assert.equal(resultStamp(stamp, true), "約 26-10-11 12:34:56");
  assert.equal(resultStamp(null), "時間未記錄");
});

test("issue report retains exact selected result and cue IDs even with identical labels", () => {
  const location = {project: "p", track: "t", transcript: "v", translation: "zh", cue: "cue-7"};
  const report = issueReport({projectTitle: "作品", trackName: "音軌", transcriptLabel: "Whisper + 對齊",
    translationLabel: "Index", location,
    cue: {ordinal: 6, start_ms: 29000, end_ms: 30300, ja: "はい", zh: null, provenance: {native: [29000,30300]}}});
  assert.ok(report.includes(JSON.stringify(location)));
  assert.ok(report.includes("#7 · 29.000–30.300 秒"));
  assert.ok(report.includes("中文：（無）"));
  assert.ok(report.includes("Whisper + 對齊"));
});
