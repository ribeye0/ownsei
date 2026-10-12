import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
async function load(name) {
    const code = ts.transpileModule(readFileSync(new URL(`../src/${name}.ts`,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
    return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const {readEntries} = await load('terminology');
const {switchTarget} = await load('translationPreferencesState');
test('row and source round trips keep disabled terms and accept old JSON objects', () => {
    const old = readEntries('{"名前":"譯名"}');
    assert.deepEqual(old,[{ja:'名前',zh:'譯名',enabled:true}]);
    const rows=[...old,{ja:'語',zh:'用詞',enabled:false}];
    assert.deepEqual(readEntries(JSON.stringify(rows)),rows);
    assert.deepEqual(readEntries('[]'),[]);
});
test('invalid drafts stay available; strict save and raw-to-row conversion reject blanks, duplicates and bad shapes', () => {
    const draft=JSON.stringify([{ja:'',zh:'',enabled:false}]);
    assert.equal(readEntries(draft,false)[0].enabled,false);
    assert.throws(()=>readEntries(draft),/不可空白/);
    assert.throws(()=>readEntries('[{"ja":" 語 ","zh":"用詞"},{"ja":"語","zh":"用詞"}]'),/重複/);
    for(const value of ['null','[{}]','[{"ja":"語","zh":"用詞","enabled":1}]','{"語":3}','[{"ja":"語","zh":"用詞","typo":true}]','[{']) {
        assert.throws(()=>readEntries(value));
    }
});
test('language switches preserve each custom style and use the other language factory prompt initially', () => {
    const defaults={'zh-Hant':'繁體預設','zh-Hans':'简体默认'};
    const old={target_language:'zh-Hant',translation_style:'繁體自訂'};
    const first=switchTarget(old,'zh-Hans',defaults);
    assert.equal(first.translation_style,'简体默认');
    const second=switchTarget({...first,translation_style:'简体自订'},'zh-Hant',defaults);
    assert.equal(second.translation_style,'繁體自訂');
    assert.equal(switchTarget(second,'zh-Hans',defaults).translation_style,'简体自订');
    assert.deepEqual(old,{target_language:'zh-Hant',translation_style:'繁體自訂'});
});
