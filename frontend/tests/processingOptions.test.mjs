import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = readFileSync(new URL('../src/processingOptions.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
const {missingProcessingModels} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const catalog = ['haas-ja-v1.0-f16','whisper-ja-760m','index-nativelong-9b','firered-vad','silero-vad']
  .map(id => ({id, ready: false}));
const execution = {vad_engine: 'firered'}, filter = {enabled: false};
const missing = (mode, asr, executionOverride={}, filterOverride={}) => missingProcessingModels(
  catalog, mode, asr, 'index-nativelong-9b', {...execution,...executionOverride}, {...filter,...filterOverride},
).map(m => m.id);
test('single and batch processing require only models used by the selected stages', () => {
  assert.deepEqual(missing('translate','haas-ja-v1.0-f16'), ['index-nativelong-9b']);
  assert.deepEqual(missing('transcribe','whisper-ja-760m'), ['whisper-ja-760m']);
  assert.deepEqual(missing('run','haas-ja-v1.0-f16'), ['haas-ja-v1.0-f16','index-nativelong-9b','firered-vad']);
});
test('Silero segmentation and FireRed filtering have independent requirements without duplicate entries', () => {
  assert.deepEqual(missing('transcribe','haas-ja-v1.0-f16',{vad_engine:'none'}), ['haas-ja-v1.0-f16']);
  assert.deepEqual(missing('transcribe','haas-ja-v1.0-f16',{vad_engine:'silero'},{enabled:true}),
    ['haas-ja-v1.0-f16','firered-vad','silero-vad']);
  assert.deepEqual(missing('transcribe','haas-ja-v1.0-f16',{}, {enabled:true}), ['haas-ja-v1.0-f16','firered-vad']);
  assert.deepEqual(missingProcessingModels(catalog.map(m => ({...m,ready:true})), 'run', 'haas-ja-v1.0-f16',
    'index-nativelong-9b', execution, filter), []);
});
