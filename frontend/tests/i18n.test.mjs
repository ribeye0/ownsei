import assert from "node:assert/strict";
import {readFileSync,readdirSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
const dictionary=JSON.parse(readFileSync(new URL('../src/locales/zh-Hans.json',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/i18n.ts',import.meta.url),'utf8').replace('import simplified from "./locales/zh-Hans.json";',`const simplified=${JSON.stringify(dictionary)};`);
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {t,locale,setLocale}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const saved=new Map();
globalThis.localStorage={getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)};
globalThis.document={documentElement:{lang:''}};
globalThis.window={dispatchEvent:()=>true};

test('UI language is independent of translation settings and never converts unknown user text',()=>{
  assert.equal(locale(),'zh-Hant');assert.equal(t('批量處理'),'批量處理');
  setLocale('zh-Hans');assert.equal(t('批量處理'),'批量处理');assert.equal(document.documentElement.lang,'zh-CN');
  assert.equal(t('作品名：歡迎回來／こんにちは'),'作品名：歡迎回來／こんにちは');
  assert.equal(saved.has('target_language'),false);
  setLocale('zh-Hant');
});

test('every explicit application label has a simplified translation',()=>{
  const directory=new URL('../src/',import.meta.url);
  const missing=[];
  for(const name of readdirSync(directory).filter(n=>n.endsWith('.tsx'))) {
    const file=ts.createSourceFile(name,readFileSync(new URL(name,directory),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    function walk(node) {
      if(ts.isCallExpression(node)&&node.expression.getText(file)==='ui'&&ts.isStringLiteral(node.arguments[0])&&/[\u3400-\u9fff]/.test(node.arguments[0].text)&&!(node.arguments[0].text in dictionary))missing.push([name,node.arguments[0].text]);
      ts.forEachChild(node,walk);
    }
    walk(file);
  }
  assert.deepEqual(missing,[]);
});
