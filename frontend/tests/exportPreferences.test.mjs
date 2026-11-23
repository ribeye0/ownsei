import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = readFileSync(new URL('../src/exportPreferences.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
const {loadExportPreferences, saveExportPreferences, normalizeExportPreferences} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const saved = new Map();
globalThis.localStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};

test('first export defaults to Chinese VTT and explicit save survives reopening across projects', () => {
  saved.clear();
  assert.deepEqual(loadExportPreferences(), {languages:['zh'], formats:['vtt'], chinese:'as-result', languageTag:false, destination:'download', overwrite:false});
  const preferences = {languages:['zh','ja'], formats:['vtt','srt'], chinese:'zh-Hans', languageTag:true, destination:'sub', overwrite:true};
  const draft = {...preferences, track:'one-track', result_id:'one-result'};
  assert.deepEqual(loadExportPreferences().languages, ['zh']); // Editing alone does not save.
  saveExportPreferences(draft);
  assert.deepEqual(loadExportPreferences(), preferences);
  assert.equal(Object.values(JSON.parse([...saved.values()][0])).includes('one-track'), false);
});

test('malformed saved preferences recover valid selections and independent factory defaults', () => {
  saved.clear();
  saved.set('ownsei:export-preferences:v1', '{bad json');
  assert.deepEqual(loadExportPreferences().languages, ['zh']);
  for (const value of [null, [], 42]) assert.equal(normalizeExportPreferences(value).destination, 'download');
  assert.deepEqual(normalizeExportPreferences({languages:['invalid','zh','zh'], formats:[], chinese:'bad', languageTag:'false', destination:'../bad', overwrite:'true'}),
    {languages:['zh'], formats:['vtt'], chinese:'as-result', languageTag:false, destination:'download', overwrite:false});
});

test('unavailable browser storage keeps opening usable and saving reports failure', () => {
  const storage = globalThis.localStorage;
  globalThis.localStorage = {getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}};
  try {
    assert.deepEqual(loadExportPreferences().languages, ['zh']);
    assert.throws(() => saveExportPreferences(normalizeExportPreferences(null)), /blocked/);
  } finally {globalThis.localStorage = storage;}
});


test('removed Markdown defaults migrate safely and LRC preferences persist', () => {
  assert.deepEqual(normalizeExportPreferences({formats:['md','timed.md']}).formats, ['vtt']);
  assert.deepEqual(normalizeExportPreferences({formats:['md','srt']}).formats, ['srt']);
  const preferences = {...normalizeExportPreferences(null), formats:['lrc','ass']};
  saveExportPreferences(preferences);
  assert.deepEqual(loadExportPreferences(), preferences);
});
