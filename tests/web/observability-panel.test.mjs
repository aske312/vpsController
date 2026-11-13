import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';

function mount() {
  const slots = [], effects = [], calls = [];
  let cursor = 0;
  const hooks = {
    useState(initial) { const index=cursor++; if (!(index in slots)) slots[index]=initial; return [slots[index], value => slots[index]=typeof value==='function'?value(slots[index]):value]; },
    useRef(initial) { const index=cursor++; return slots[index] ||= {current:initial}; },
    useCallback(fn) { cursor++; return fn; },
    useEffect(fn, deps) { const index=cursor++; if (!slots[index] || deps.some((value,i)=>value!==slots[index][i])) {slots[index]=deps;effects.push(fn);} },
  };
  const policy={enabled:true,errors_only:false,retention_days:7,max_events:1000,revision:0};
  const request=async path => {calls.push(path); return path.includes('/settings')?policy:{settings:policy,summary:{requests:0,errors:0,affected_profiles:0,first_error_at:null,last_error_at:null},subscriptions:{succeeded:0,failed:0},events:[]};};
  const actual=createRequire(import.meta.url), exports={};
  const source=readFileSync(new URL('../../src/features/services/observability-panel.tsx',import.meta.url),'utf8');
  const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','exports',output)(name=>name==='react'?hooks:name.endsWith('/api-request')?{}:actual(name),exports);
  return {calls,render(){cursor=0;const tree=exports.ObservabilityPanel({request,busy:false});for(const effect of effects.splice(0))effect();return tree;}};
}
function nodes(tree, match) {
  if(Array.isArray(tree))return tree.flatMap(value=>nodes(value,match));
  if(!tree?.props)return [];
  return [...(match(tree)?[tree]:[]),...nodes(tree.props.children,match)];
}
test('events remain hidden until explicitly shown, and each read uses the selected row limit',async()=>{
  const view=mount();view.render();await Promise.resolve();let tree=view.render();
  assert.deepEqual(view.calls,['/observability/settings']);
  assert.equal(nodes(tree,node=>node.props['aria-label']==='Последние события').length,0);
  for(const limit of [10,50,100]){
    nodes(tree,node=>node.props['aria-label']==='Количество событий')[0].props.onChange({target:{value:String(limit)}});
    tree=view.render();
    await nodes(tree,node=>node.type==='button'&&node.props.children==='Показать')[0].props.onClick();
    await Promise.resolve();tree=view.render();
    assert.equal(new URL('https://example.com'+view.calls.at(-1)).searchParams.get('limit'),String(limit));
    assert.equal(nodes(tree,node=>node.props['aria-label']==='Последние события').length,1);
  }
});
