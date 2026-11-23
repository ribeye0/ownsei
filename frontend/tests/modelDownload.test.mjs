import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/modelDownload.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}});
const {modelDownload} = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
const job = {bytes_done: 0, bytes_total: 1000, files_total: 1};

test('download bar advances with the network while buffered writes stand still', () => {
  for (const received of [0, 200, 750]) {
    const progress = modelDownload({...job, transfer_bytes_done: received}, 1000);
    assert.equal(progress.percent, received / 10);
    assert.equal(progress.received, received);
  }
});

test('cached file bytes are not counted again as network traffic on retry', () => {
  assert.equal(modelDownload({...job, bytes_done: 700, transfer_bytes_done: 50}, 1000).percent, 5);
  assert.equal(modelDownload({...job, bytes_done: 700}, 1000).percent, 70);
});

test('use a source estimate before all files are known and drop it when traffic exceeds it', () => {
  const partial = {...job, files_total: 3, bytes_total: 10, transfer_bytes_done: 200};
  assert.equal(modelDownload(partial, 1000).percent, 20);
  assert.equal(modelDownload({...partial, transfer_bytes_done: 1200}, 1000).percent, undefined);
  assert.equal(modelDownload({...job, bytes_total: 0, transfer_bytes_done: 0}, 0).percent, undefined);
});
