import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

function harness() {
  const require = createRequire(import.meta.url);
  const states=[]; let cursor=0;
  const compiled=ts.transpileModule(readFileSync(new URL('../app/connection-profile.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const loaded={exports:{}};
  new Function('require','module','exports',compiled)(name=>name==='react'?{useEffect(){},useState(initial){const index=cursor++;if(!(index in states))states[index]=initial;return [states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value;}];}}:name==='qrcode'?{}:name==='next/image'?()=>null:name==='./client-apps'?{ClientAppCatalog(){}}:name==='./protocol-icon'?{ProtocolIcon(){}}:require(name),loaded,loaded.exports);
  const profile={protocol:'tuic',name:'QA',endpoint:'example.test',fields:[{label:'Пароль',value:'SECRET-QA',secret:true}],apps:[],steps:[],delivery:{file:{filename:'qa.json',content:'{}',mime_type:'application/json'}}};
  return ()=>{cursor=0;return loaded.exports.ConnectionProfileResult({profile,onDownload(){}});};
}
function elements(node){if(Array.isArray(node))return node.flatMap(elements);if(!node||typeof node!=='object')return [];return [node,...elements(node.props?.children)];}

test('credentials are masked until explicitly revealed and can be hidden again',()=>{
  const render=harness();let nodes=elements(render());
  assert.ok(nodes.some(n=>n.type==='strong'&&n.props.children==='••••••••'));
  assert.ok(!nodes.some(n=>n.props?.children==='SECRET-QA'));
  nodes.find(n=>n.props?.['aria-label']==='Показать Пароль').props.onClick();nodes=elements(render());
  assert.ok(nodes.some(n=>n.type==='strong'&&n.props.children==='SECRET-QA'));
  nodes.find(n=>n.props?.['aria-label']==='Скрыть Пароль').props.onClick();nodes=elements(render());
  assert.ok(!nodes.some(n=>n.props?.children==='SECRET-QA'));
});

test('a denied clipboard does not report successful copying or leak the secret',async()=>{
  const render=harness();const navigatorDescriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator');const savedWindow=globalThis.window;
  try{
    Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async()=>{throw new Error('Denied');}}}});
    globalThis.window={isSecureContext:true};
    await elements(render()).find(n=>n.type==='button'&&n.props.children==='копировать').props.onClick();
    await Promise.resolve();await Promise.resolve();
    const nodes=elements(render());
    assert.ok(nodes.some(n=>n.props?.role==='alert'));
    assert.ok(!nodes.some(n=>n.props?.children==='готово'||n.props?.children==='SECRET-QA'));
  }finally{if(navigatorDescriptor)Object.defineProperty(globalThis,'navigator',navigatorDescriptor);else delete globalThis.navigator;globalThis.window=savedWindow;}
});
