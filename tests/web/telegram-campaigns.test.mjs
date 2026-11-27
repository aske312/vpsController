import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree?.props ? [tree, ...nodes(tree.props.children)] : [];
const text = tree => Array.isArray(tree) ? tree.map(text).join('') : tree?.props ? text(tree.props.children) : String(tree ?? '');
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function mount({ ready = true, rejectSend = false } = {}) {
  let cursor = 0, saved;
  const slots = [], effects = [], writes = [], dirty = [];
  const same = (a,b) => a && b && a.length === b.length && a.every((v,i) => Object.is(v,b[i]));
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useCallback(fn,deps) { const i = cursor++; if (!same(slots[i]?.deps,deps)) slots[i] = { deps,fn }; return slots[i].fn; },
    useEffect(fn,deps) { const i = cursor++; if (!same(slots[i]?.deps,deps)) { slots[i]?.cleanup?.(); slots[i] = {deps}; effects.push(() => { slots[i].cleanup = fn(); }); } },
  };
  const Dialog = () => null, actual = createRequire(import.meta.url), exported = {};
  const source = readFileSync(new URL('../../src/features/assistant/telegram-campaigns.tsx',import.meta.url),'utf8');
  const compiled = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','exports',compiled)(name => name === 'react' ? hooks : name.endsWith('.css') ? {} : name === './nodera-settings' ? {SettingsDialog:Dialog} : name === './chat-icon' ? {ChatIcon:()=>null} : name.endsWith('/notification-center') ? {useNotifier:()=>({success(){}}),usePageError(){},NotificationNotice:()=>null} : actual(name),exported);
  const recipients = [{telegram_id:22,name:'Мария',eligible:true,subscriber:true}];
  const request = async (path,options) => {
    if (!options?.method) return path === '/assistant/campaigns' ? {ready,recipients,items:saved?[saved]:[]} : saved;
    const body = options.body && JSON.parse(options.body); writes.push({path,body});
    if (path === '/assistant/campaigns') return saved = {...body,revision:1,state:'draft'};
    if (path.endsWith('/preview')) return {campaign:saved,confirmation:'a'.repeat(32),recipients,excluded:[]};
    if (path.endsWith('/send')) { if (rejectSend) throw new Error('Соединение потеряно'); return {...saved,state:'active'}; }
    throw new Error(path);
  };
  const props = {request,active:true,blocked:false,onDirtyChange:value=>dirty.push(value),onUpdate:async()=>{}};
  return {writes,dirty,Dialog,props,render(){cursor=0; const tree=exported.TelegramCampaigns(props); effects.splice(0).forEach(fn=>fn()); return tree;},close(){slots.forEach(slot=>slot?.cleanup?.());}};
}
function button(tree,label) { return nodes(tree).find(node=>node.type==='button' && text(node)===label); }
function field(tree,label) { return nodes(nodes(tree).find(node=>node.type==='label' && text(node).startsWith(label))).find(node=>['input','textarea','select'].includes(node.type)); }
async function edit(view) {
  view.render(); await flush(); button(view.render(),'Создать рассылку').props.onClick();
  field(view.render(),'Название').props.onChange({target:{value:'Плановые работы'}});
  field(view.render(),'Текст уведомления').props.onChange({target:{value:'Начало в 02:00'}});
}

test('editing a multi-question survey stays local and tracks unsaved changes',async()=>{
  const view=mount(); try {
    await edit(view);
    field(view.render(),'Формат').props.onChange({target:{value:'survey'}});
    button(view.render(),'Добавить вопрос').props.onClick();
    const kinds=nodes(view.render()).filter(node=>node.type==='select' && node.props.value==='choice');
    assert.equal(kinds.length,2); kinds[1].props.onChange({target:{value:'text'}});
    const questions=nodes(view.render()).filter(node=>node.type==='details' && node.props.className==='campaignQuestion');
    assert.deepEqual(questions.map(node=>node.props.open),[false,true]);
    assert.equal(nodes(view.render()).filter(node=>node.type==='select' && node.props.value==='text').length,1);
    assert.equal(view.writes.length,0); assert.equal(view.dirty.at(-1),true);
    let revealed=false;
    nodes(view.render()).find(node=>node.type==='form').props.onInvalidCapture({target:{closest:selector=>{assert.equal(selector,'details');return {setAttribute:key=>{assert.equal(key,'open');revealed=true;}};}}});
    assert.equal(revealed,true);
    view.props.active=false; assert.equal(view.render().props.hidden,true);
    view.props.active=true; assert.equal(field(view.render(),'Название').props.value,'Плановые работы');
  } finally {view.close();}
});

test('preview saves without sending, and ambiguous launch retries reuse the confirmation',async()=>{
  const view=mount({rejectSend:true}); try {
    await edit(view); button(view.render(),'Предварительный просмотр').props.onClick(); await flush();
    assert.deepEqual(view.writes.map(item=>item.path.split('/').at(-1)),['campaigns','preview']);
    let dialog=nodes(view.render()).find(node=>node.type===view.Dialog);
    assert.equal(dialog.props.submitLabel,'Отправить 1 пользователям');
    dialog.props.submit(); dialog.props.submit(); await flush();
    assert.equal(view.writes.filter(item=>item.path.endsWith('/send')).length,1);
    dialog=nodes(view.render()).find(node=>node.type===view.Dialog); assert.ok(dialog);
    dialog.props.submit(); await flush();
    const sends=view.writes.filter(item=>item.path.endsWith('/send'));
    assert.equal(sends.length,2); assert.deepEqual(sends[0],sends[1]);
  } finally {view.close();}
});

test('an outdated bot allows drafts but keeps launch preview disabled',async()=>{
  const view=mount({ready:false}); try {await edit(view); assert.equal(button(view.render(),'Предварительный просмотр').props.disabled,true); assert.equal(button(view.render(),'Сохранить черновик').props.disabled,false); assert.equal(view.writes.length,0);} finally {view.close();}
});
