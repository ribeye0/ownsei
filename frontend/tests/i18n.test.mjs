import { preferencesFixture } from "./preferencesFixture.mjs";
import assert from "node:assert/strict";
import {readFileSync,readdirSync} from "node:fs";
import test from "node:test";
import ts from "typescript";
const dictionary=JSON.parse(readFileSync(new URL('../src/locales/zh-Hans.json',import.meta.url),'utf8'));
const fixture = await preferencesFixture("i18n");
const {t,locale,setLocale,localePreference,systemLocale}=fixture.client;
let browserLanguages = ['en-US'];
Object.defineProperty(globalThis, 'navigator', {configurable:true,value:{get languages(){return browserLanguages;},get language(){return browserLanguages[0] || 'en-US';}}});
globalThis.document={documentElement:{lang:''}};
globalThis.window={dispatchEvent:()=>true};

test('UI language is independent of translation settings and never converts unknown user text',()=>{
  assert.equal(locale(),'zh-Hant');assert.equal(t('批量處理'),'批量處理');
  setLocale('zh-Hans');assert.equal(t('批量處理'),'批量处理');assert.equal(document.documentElement.lang,'zh-CN');
  assert.equal(t('作品名：歡迎回來／こんにちは'),'作品名：歡迎回來／こんにちは');
  assert.equal('target_language' in fixture.disk(),false);
  setLocale('zh-Hant');
});

test('every explicit application label has a simplified translation',()=>{
  const directory=new URL('../src/',import.meta.url);
  const missing=[];
  for(const name of readdirSync(directory).filter(n=>/\.tsx?$/.test(n))) {
    const file=ts.createSourceFile(name,readFileSync(new URL(name,directory),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    function walk(node) {
      // TSX constants also supply dynamic labels (for example VAD presets).
      if(name.endsWith('.tsx')&&ts.isStringLiteral(node)&&/[\u3400-\u9fff]/.test(node.text)&&!(node.text in dictionary))missing.push([name,node.text]);
      if(ts.isCallExpression(node)&&node.expression.getText(file)==='ui'){
        function checkLiteral(argument){
          if(ts.isStringLiteral(argument)&&/[\u3400-\u9fff]/.test(argument.text)&&!(argument.text in dictionary))missing.push([name,argument.text]);
          ts.forEachChild(argument,checkLiteral);
        }
        for(const argument of node.arguments)checkLiteral(argument);
      }
      ts.forEachChild(node,walk);
    }
    walk(file);
  }
  assert.deepEqual(missing,[]);
});


test('system language detection respects Chinese scripts, regions and preference order',()=>{
  for (const tag of ['zh-TW','zh-HK','zh-MO','zh-Hant','zh-Hant-CN','zh_CHt']) assert.equal(systemLocale([tag]),'zh-Hant',tag);
  for (const tag of ['zh-CN','zh-SG','zh-MY','zh-Hans','zh-Hans-TW','zh','ZH_cn']) assert.equal(systemLocale([tag]),'zh-Hans',tag);
  assert.equal(systemLocale(['en-US','ja-JP','zh-CN']),'zh-Hans');
  assert.equal(systemLocale(['zh-TW','zh-CN']),'zh-Hant');
  assert.equal(systemLocale(['zh-CN','zh-TW']),'zh-Hans');
  assert.equal(systemLocale(['en-US','ja-JP']),'zh-Hant');
  assert.equal(systemLocale([]),'zh-Hant');
});

test('first visit follows system; saved manual choices survive and auto can be restored',async ()=>{
  await fixture.reset();browserLanguages=['zh-CN'];
  assert.equal(localePreference(),'auto');assert.equal(locale(),'zh-Hans');
  setLocale('zh-Hant');assert.equal(localePreference(),'zh-Hant');assert.equal(locale(),'zh-Hant');
  browserLanguages=['zh-TW'];setLocale('zh-Hans');assert.equal(locale(),'zh-Hans');
  setLocale('auto');assert.equal(localePreference(),'auto');assert.equal(locale(),'zh-Hant');
  browserLanguages=['zh-CN'];assert.equal(locale(),'zh-Hans');
  await fixture.reset({locale:'invalid'});assert.equal(localePreference(),'auto');
  assert.equal(t('匯出字幕'),'导出字幕');assert.equal(t('較敏感（預設）'),'较敏感（默认）');
  setLocale('zh-Hant');browserLanguages=['en-US'];
});

test('blocked browser storage still permits language switching for this session',async ()=>{
  await fixture.reset();
  const storage=globalThis.localStorage;
  globalThis.localStorage={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  browserLanguages=['zh-CN'];
  try {
    await fixture.settings.loadInterfacePreferences();
    assert.equal(locale(),'zh-Hans');setLocale('zh-Hant');assert.equal(locale(),'zh-Hant');
    setLocale('auto');assert.equal(locale(),'zh-Hans');
  } finally {globalThis.localStorage=storage;browserLanguages=['en-US'];setLocale('zh-Hant');}
});

test('visible labels and accessibility attributes do not bypass localization',()=>{
  const directory=new URL('../src/',import.meta.url),missing=[];
  const languageNames=new Set(['繁體中文','简体中文']); // Autonyms identify language choices in either interface.
  for(const name of readdirSync(directory).filter(n=>n.endsWith('.tsx'))){
    const file=ts.createSourceFile(name,readFileSync(new URL(name,directory),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    function walk(node){
      if(ts.isJsxText(node)){
        const text=node.getText(file).trim();
        if(/[\u3400-\u9fff]/.test(text)&&!languageNames.has(text))missing.push([name,text]);
      }
      if(ts.isJsxAttribute(node)&&node.initializer&&ts.isStringLiteral(node.initializer)&&/[\u3400-\u9fff]/.test(node.initializer.text))missing.push([name,node.getText(file)]);
      ts.forEachChild(node,walk);
    }
    walk(file);
  }
  assert.deepEqual(missing,[]);
});
