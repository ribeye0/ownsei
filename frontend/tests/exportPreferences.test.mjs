import assert from "node:assert/strict";
import test from "node:test";
import { preferencesFixture } from "./preferencesFixture.mjs";
const fixture = await preferencesFixture("exportPreferences");
const {loadExportPreferences, saveExportPreferences, normalizeExportPreferences} = fixture.client;

test('first export defaults to Chinese VTT and explicit save survives reopening across projects', async () => {
  await fixture.reset();
  assert.deepEqual(loadExportPreferences(), {languages:['zh'], formats:['vtt'], chinese:'as-result', languageTag:false, destination:'download', overwrite:false});
  const preferences = {languages:['zh','ja'], formats:['vtt','srt'], chinese:'zh-Hans', languageTag:true, destination:'sub', overwrite:true};
  const draft = {...preferences, track:'one-track', result_id:'one-result'};
  assert.deepEqual(loadExportPreferences().languages, ['zh']); // Editing alone does not save.
  await saveExportPreferences(draft);
  assert.deepEqual(loadExportPreferences(), preferences);
  assert.equal(Object.values(fixture.disk().export).includes('one-track'), false);
});

test('malformed saved preferences recover valid selections and independent factory defaults', async () => {
  await fixture.reset();
  fixture.legacy.set('ownsei:export-preferences:v1', '{bad json');
  await fixture.settings.loadInterfacePreferences();
  assert.deepEqual(loadExportPreferences().languages, ['zh']);
  for (const value of [null, [], 42]) assert.equal(normalizeExportPreferences(value).destination, 'download');
  assert.deepEqual(normalizeExportPreferences({languages:['invalid','zh','zh'], formats:[], chinese:'bad', languageTag:'false', destination:'../bad', overwrite:'true'}),
    {languages:['zh'], formats:['vtt'], chinese:'as-result', languageTag:false, destination:'download', overwrite:false});
});

test('disk write failures reach the explicit save action', async () => {
  await fixture.reset();
  fixture.failWrites(true);
  try {
    assert.deepEqual(loadExportPreferences().languages, ['zh']);
    await assert.rejects(saveExportPreferences(normalizeExportPreferences(null)), /disk write failed/);
  } finally {fixture.failWrites(false);}
});


test('removed Markdown defaults migrate safely and LRC preferences persist', async () => {
  assert.deepEqual(normalizeExportPreferences({formats:['md','timed.md']}).formats, ['vtt']);
  assert.deepEqual(normalizeExportPreferences({formats:['md','srt']}).formats, ['srt']);
  const preferences = {...normalizeExportPreferences(null), formats:['lrc','ass']};
  await saveExportPreferences(preferences);
  assert.deepEqual(loadExportPreferences(), preferences);
});

test("a fresh browser shares saved options and legacy import only fills missing keys", async () => {
  await fixture.reset({theme: "mocha"});
  fixture.legacy.set("ownsei:theme", "classic");
  fixture.legacy.set("ownsei:export-preferences:v1", JSON.stringify({formats: ["ass"]}));
  await fixture.settings.loadInterfacePreferences();
  fixture.legacy.clear();
  await fixture.settings.loadInterfacePreferences();
  assert.deepEqual(loadExportPreferences().formats, ["ass"]);
  assert.equal(fixture.disk().theme, "mocha");
});
