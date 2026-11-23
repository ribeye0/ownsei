import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
const source=readFileSync(new URL('../src/jobState.ts',import.meta.url),'utf8');
const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {stageProgress}=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const job={action:'run',status:'running',progress:.65,tracks:[{id:'t'}],current_track:'t',current_stage:'translate',stages:{t:{transcribe:{status:'completed',progress:1},translate:{status:'running',progress:.25}}}};
test('transcription and translation have independent progress, and a failure retains its position',()=>{
 let rows=stageProgress(job);
 assert.deepEqual(rows.map(r=>[r.name,r.value,r.done,r.total]),[['transcribe',1,1,1],['translate',.25,0,1]]);
 rows=stageProgress({...job,status:'partial',stages:{t:{transcribe:{status:'completed'},translate:{status:'failed',progress:.25}}}});
 assert.equal(rows[1].value,.25);assert.equal(rows[1].status,'failed');
});
test('batch counters include waiting tracks, and resumed stages count once',()=>{
 const rows=stageProgress({...job,tracks:[{id:'a'},{id:'b'},{id:'c'}],tracks_total:3,current_track:'b',stages:{a:{transcribe:{status:'completed'},translate:{status:'completed'}},b:{transcribe:{status:'completed',resumed_from:'old'},translate:{status:'running',progress:.5}}}});
 assert.equal(rows[0].value,2/3);assert.equal(rows[0].done,2);
 assert.equal(rows[1].value,.5);assert.equal(rows[1].done,1);assert.equal(rows[1].total,3);
});
test('old weighted jobs retain useful per-stage progress without new metadata',()=>{
 const rows=stageProgress({...job,status:'partial',track_progress:.8009803921568628,stages:{t:{transcribe:{status:'completed'},translate:{status:'failed'}}}});
 assert.equal(rows[0].value,1);assert.ok(Math.abs(rows[1].value-128/204)<1e-10);
});
test('single-purpose jobs show only their own stage; queued tracks and invalid counters stay safe',()=>{
 assert.deepEqual(stageProgress({...job,action:'transcribe'}).map(r=>r.name),['transcribe']);
 assert.deepEqual(stageProgress({...job,action:'translate'}).map(r=>r.name),['translate']);
 const rows=stageProgress({action:'run',status:'queued',progress:0,options:{track_ids:['a','b']}});
 assert.deepEqual(rows.map(r=>[r.value,r.total,r.status]),[[0,2,'pending'],[0,2,'pending']]);
 assert.equal(stageProgress({action:'export',status:'completed',progress:1})[0].value,1);
 assert.equal(stageProgress({...job,stages:{t:{translate:{status:'running',progress:NaN}}}})[1].value,0);
});
