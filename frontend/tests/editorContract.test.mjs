import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import ts from "typescript";

async function load(name) {
  const source=readFileSync(new URL(`../src/${name}.ts`,import.meta.url),"utf8");
  const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
}
const {editRequest,dirtyDraft}=await load("editorContract");
const {liveJob,finishedJobs}=await load("jobState");
const cue={id:"c",revision:2,ja:"はい",zh:"好",ja_override:null,zh_override:null,start_ms:0,end_ms:1000,review_flags:[],provenance:{translation_version:"index-result"}};

test("draft uses the opening revision and Chinese result even after a background refresh",()=>{
  const original=structuredClone(cue), draft={...cue,zh:"校對"};
  const refreshed={...cue,revision:5,zh:"背景更新",provenance:{translation_version:"qwen-result"}};
  const request=editRequest(original,draft);
  assert.equal(request.revision,2);
  assert.equal(request.translation_version,"index-result");
  assert.equal(request.changes.zh_override,"校對");
  assert.notEqual(request.revision,refreshed.revision);
  assert.deepEqual(request.changes.review_flags,[]);
});
test("untouched fields preserve machine provenance, while human text, timing and flags are dirty",()=>{
  assert.equal(dirtyDraft(cue,structuredClone(cue)),false);
  for(const changes of [{ja:"修正"},{zh:"修改"},{start_ms:10},{end_ms:900},{review_flags:["manual_review"]}]) {
    assert.equal(dirtyDraft(cue,{...cue,...changes}),true);
  }
  assert.deepEqual(editRequest(cue,cue).changes,{ja_override:null,zh_override:null,start_ms:0,end_ms:1000,review_flags:[]});
});
test("cancelling keeps mutation protection, and partial completion wakes result refresh",()=>{
  assert.equal(liveJob("cancelling"),true);
  assert.equal(liveJob("partial"),false);
  assert.equal(finishedJobs([{id:"a",status:"cancelling"},{id:"b",status:"partial"}]),"bpartial");
});
