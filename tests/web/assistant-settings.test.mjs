import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as chatCommands from '../../src/features/assistant/chat-commands.ts';
import * as chatSession from '../../src/features/assistant/chat-session.ts';
import * as commandMessages from '../../src/features/assistant/command-message-state.ts';
import * as chatJobs from '../../src/features/assistant/chat-jobs.ts';
import * as helpers from '../../src/features/assistant/assistant-state.ts';
import * as chatFiles from '../../src/features/assistant/chat-files.ts';
import * as answerFormat from '../../src/features/assistant/answer-format.ts';
import * as providerConnection from '../../src/features/assistant/provider-connection.ts';
import * as providerDetails from '../../src/features/assistant/provider-details.ts';

// Render the real form with controlled hooks and I/O, including disabled ancestors.
function mount(status, catalog = [], overrides = {}) {
  const slots = [], effects = [], writes = [], notifications = [], persistence = [];
  let cursor = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const hooks = {
    createContext: () => ({}), useContext: () => confirmations,
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useId() { return `fixture-${cursor++}`; },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { deps, fn };
      return slots[index].fn;
    },
    useMemo(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { deps, value: fn() };
      return slots[index].value;
    },
    memo: component => component,
    useEffect(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) { slots[index] = { deps }; effects.push(fn); }
    },
  };
  const requireActual = createRequire(import.meta.url), exported = {};
  const source = readFileSync(new URL('../../src/features/assistant/assistant-view.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const settings = {}, alerts = {};
  const confirmationModule = {}, commandMenuModule = {}, turnModule = {};
  const activityModule = { ChatActivity({label}) { return requireActual('react/jsx-runtime').jsx('div', {role:'status',children:label}); } };
  const sessionHelpers = { ...chatSession, createChatSessionWriter(...args) { const writer = chatSession.createChatSessionWriter(...args); persistence.push(writer); return writer; } };
  const confirmations = { actions: [], working: [], results: {}, errors: {}, ingest: (actor, actions) => { confirmations.actions = actions.map(item => ({...item,actor})); }, async decide() {} };
  const require = name => name.endsWith('/command-message-state') ? commandMessages : name === 'react' ? hooks : name.endsWith('/use-dialog-focus') ? {useDialogFocus:()=>({current:null})} : name === './chat-session' ? sessionHelpers : name === './chat-activity' ? activityModule : name === './chat-turn' ? turnModule : name === './chat-commands' ? chatCommands : name === './chat-command-menu' ? commandMenuModule : name === './confirmation-queue' ? confirmationModule : name === './chat-jobs' ? chatJobs : name === './assistant-state' ? helpers : name === './nodera-settings' ? settings : name === './telegram-alerts' ? alerts : name === './chat-files' ? chatFiles : name === './answer-format' ? answerFormat : name === './assistant-answer' ? { AssistantAnswer() {} } : name === './chat-icon' ? { ChatIcon() {} }
    : name === './alert-watches' ? { AlertWatches() {} } : name === './alert-diagnostic-rules' ? { AlertDiagnosticRules() {} } : name === './provider-connection' ? providerConnection : name === './provider-details' ? providerDetails : name.endsWith('/notification-center') ? { usePageError: (source, title, message, action) => {
      const id = `page-error:${source}`, index = notifications.findIndex(item => item.id === id);
      if (index >= 0) notifications.splice(index, 1);
      if (message) notifications.push({ id, title, message, action, state: 'error' });
    }, useNotifications: () => ({ getSnapshot: () => notifications.filter(item => item.id), resolve: id => { const index = notifications.findIndex(item => item.id === id); if (index >= 0) notifications.splice(index, 1); }, upsert: value => notifications.push(value) }), useNotifier: () => ({ error: message => notifications.push({state:'error',message}), success: message => notifications.push({state:'success',message}), info: message => notifications.push({state:'info',message}) }) } : name.endsWith('/system-operation') ? { systemActionSnapshot: value => value } : name.endsWith('/api-request') ? { mutationFailureState: () => 'error' } : name.endsWith('/protocol-icon') ? { ProtocolIcon() {} } : requireActual(name);
  const alertsSource = readFileSync(new URL('../../src/features/assistant/telegram-alerts.tsx', import.meta.url), 'utf8');
  const compiledAlerts = ts.transpileModule(alertsSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', compiledAlerts.outputText)(require, alerts);
  const settingsSource = readFileSync(new URL('../../src/features/assistant/nodera-settings.tsx', import.meta.url), 'utf8');
  const compiledSettings = ts.transpileModule(settingsSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', compiledSettings.outputText)(require, settings);
  const queueSource = readFileSync(new URL('../../src/features/assistant/confirmation-queue.tsx', import.meta.url), 'utf8');
  const queueCompiled = ts.transpileModule(queueSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', queueCompiled.outputText)(require, confirmationModule);
  const commandMenuSource = readFileSync(new URL('../../src/features/assistant/chat-command-menu.tsx', import.meta.url), 'utf8');
  const commandMenuCompiled = ts.transpileModule(commandMenuSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', commandMenuCompiled.outputText)(require, commandMenuModule);
  const turnSource = readFileSync(new URL('../../src/features/assistant/chat-turn.tsx', import.meta.url), 'utf8');
  const turnCompiled = ts.transpileModule(turnSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', turnCompiled.outputText)(require, turnModule);
  new Function('require', 'exports', outputText)(require, exported);
  const jobs = overrides.jobs ?? new Map();
  const cancellations = new Map();
  const request = async (path, options) => {
    if (path.startsWith('/assistant/conversations')) return overrides.conversations ? overrides.conversations(path) : {enabled:true,items:[]};
    if (path.startsWith('/assistant/commands')) {
      if (options?.method === 'POST') { const body = JSON.parse(options.body); writes.push(body); return overrides.command ? overrides.command(body) : {text:'Command output',context_safe:true,pending_actions:[]}; }
      return { items: overrides.commands ?? [] };
    }
    if (path === '/assistant/mihomo-profiles') return { items: [{id: 'own', name: 'Моя подписка'}] };
    if (path === '/assistant/bot-users') return { items: overrides.contacts ?? [] };
    if (path.startsWith('/assistant/bot-users/')) {
      writes.push({path, method:options.method, ...(options.body ? JSON.parse(options.body) : {})});
      return overrides.contact ? overrides.contact(path, options) : {};
    }
    if (path === '/assistant/models') return overrides.models ? overrides.models(options) : { models: catalog };
    if (path.startsWith('/assistant/queue?')) return overrides.queue ? overrides.queue(path) : {state:'running', position:0, waiting:0};
    if (options?.method === 'PUT' || path === '/assistant/requests') writes.push(JSON.parse(options.body));
    if (options?.method === 'PUT' && overrides.save) return overrides.save(options);
    if (path === '/assistant/requests') {
      const payload=JSON.parse(options.body);
      const accepted={id:payload.request_id,seq:0,state:'queued',stage:'queued',question:payload.prompt,model:payload.model,stream:payload.stream};
      const result=Promise.resolve(overrides.chat ? overrides.chat(options) : {result:{answer:'OK',tools_used:[]}}).then(
        output=>({...accepted,seq:1,state:'done',stage:'complete',output}),
        error=>({...accepted,seq:1,state:'failed',stage:'error',error:{code:'provider_transport_error',message:error.message}}));
      jobs.set(payload.request_id,Promise.race([result,new Promise(resolve=>cancellations.set(payload.request_id,()=>resolve({...accepted,seq:1,state:'failed',stage:'cancelled',error:{code:'cancelled',message:'cancelled'}})))]));
      return accepted;
    }
    if (path.startsWith('/assistant/requests/')) {
      const identity=path.split('/')[3].split('?')[0];
      if(options?.method==='DELETE'){cancellations.get(identity)?.();return {};}
      return jobs.get(identity);
    }
    return status;
  };
  request.sessionKey = overrides.sessionKey;
  const props = { active: true, onOpenChat: overrides.onOpenChat || (() => {}), request, busy: overrides.busy ?? false, refreshKey: 0, onDirtyChange() {}, confirmAction: async () => false, admitOperation() {}, onOperation() {} };
  return { writes, notifications, flushSession() { for (const writer of persistence) writer.flush(); }, setActive(value) { props.active = value; }, setBusy(value) { props.busy = value; }, render() {
    cursor = 0;
    const tree = exported.AssistantView(props);
    const expand = node => {
      if (Array.isArray(node)) return node.map(expand);
      if (!node?.props) return node;
      if (node.type?.name === 'DecisionButtons' || node.type === activityModule.ChatActivity || node.type === turnModule.ChatTurnRow || node.type === commandMenuModule.ChatCommandMenu || node.type === confirmationModule.ConfirmationCards || node.type === settings.NoderaSettings) return expand(node.type(node.props));
      return { ...node, props: { ...node.props, children: expand(node.props.children) } };
    };
    const expanded = expand(tree);
    for (const effect of effects.splice(0)) effect();
    return expanded;
  } };
}
function nodes(tree, predicate, disabled = false) {
  if (Array.isArray(tree)) return tree.flatMap(child => nodes(child, predicate, disabled));
  if (!tree || typeof tree !== 'object') return [];
  const blocked = disabled || tree.props?.disabled === true;
  return [...(predicate(tree) ? [{ node: tree, disabled: blocked }] : []), ...nodes(tree.props?.children, predicate, blocked)];
}

const shortcuts = [
  {command:'/status',label:'Состояние сервера',syntax:'/status',insert:'/status',needs_args:false,kind:'direct'},
  {command:'/services',label:'Службы',syntax:'/services',insert:'/services',needs_args:false,kind:'direct'},
  {command:'/service',label:'Управление службой',syntax:'/service <ID> <start|stop|restart>',insert:'/service ',needs_args:true,kind:'direct'},
  {command:'/ai',label:'Вопрос AI',syntax:'/ai <вопрос>',insert:'/ai ',needs_args:true,kind:'ai'},
];

test('new answers preserve the reader position until the transcript is scrolled back to the bottom',async()=>{
  const previousFrame=globalThis.requestAnimationFrame,previousCancel=globalThis.cancelAnimationFrame;
  let frames=[];globalThis.requestAnimationFrame=fn=>(frames.push(fn),frames.length);globalThis.cancelAnimationFrame=()=>{};
  const flushFrames=()=>{const pending=frames;frames=[];for(const fn of pending)fn();};
  try{
    const status=fixture();Object.assign(status.settings,{model:'m',modules:{'ai-agent':true,'nodera-bot':true}});Object.assign(status.modules[0],{enabled:true,configured:true});
    const view=mount(status);view.render();await Promise.resolve();let tree=view.render();
    const log=nodes(tree,n=>n.props.className==='mcpTranscript')[0].node;
    const scroller={scrollHeight:1000,clientHeight:200,scrollTop:100};log.props.ref.current=scroller;
    log.props.onScroll({currentTarget:scroller});flushFrames();
    const send=async question=>{
      nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:question}});tree=view.render();
      nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();
      view.render();await new Promise(setImmediate);tree=view.render();flushFrames();
    };
    await send('First question');assert.equal(scroller.scrollTop,100);
    scroller.scrollTop=800;log.props.onScroll({currentTarget:scroller});
    await send('Second question');assert.equal(scroller.scrollTop,1000);
    assert.equal(view.writes.length,2);
  }finally{globalThis.requestAnimationFrame=previousFrame;globalThis.cancelAnimationFrame=previousCancel;}
});

test('guest profile edits save an optional-year birthday without adding permissions',async()=>{
  const guest={telegram_id:77,name:'Visitor',username:'visitor',seen_at:1000,birthdate:null};
  const view=mount(fixture(),[],{contacts:[guest],contact:(_path,options)=>({...guest,...JSON.parse(options.body)})});
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Visitor')[0].node.props.onClick();
  let tree=view.render();
  nodes(tree,n=>n.props['aria-label']==='Дата рождения')[0].node.props.onChange({target:{value:'29.02'}});
  tree=view.render();nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node.props.submit();
  await new Promise(setImmediate);tree=view.render();
  assert.deepEqual(view.writes,[{path:'/assistant/bot-users/77',method:'PATCH',display_name:'',birthdate:{day:29,month:2,year:null}}]);
  assert.equal(nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь').length,0);
  assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Сохранить'&&!n.props.disabled).length,0);
});

test('one user dialog applies profile and access edits together and keeps them on a profile error',async()=>{
  for (const fail of [false,true]) {
    const guest={telegram_id:77,name:'Visitor',username:'visitor',seen_at:1000,birthdate:null};
    const view=mount(fixture(),[],{contacts:[guest],contact:(_path,options)=>{if(fail)throw new Error('profile unavailable');return {...guest,...JSON.parse(options.body)};}});
    view.render();await new Promise(setImmediate);
    nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
    view.render();await new Promise(setImmediate);
    let tree=view.render();
    assert.equal(nodes(tree,n=>n.props['aria-label']==='Профиль бота Visitor').length,0);
    assert.equal(nodes(tree,n=>n.props['aria-label']==='Настроить пользователя Visitor').length,1);
    nodes(tree,n=>n.props['aria-label']==='Настроить пользователя Visitor')[0].node.props.onClick();
    tree=view.render();
    assert.equal(nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь').length,1);
    nodes(tree,n=>n.props['aria-label']==='Дата рождения')[0].node.props.onChange({target:{value:'29.02'}});
    nodes(view.render(),n=>n.type==='button'&&n.props.children?.props?.children==='Пользователь')[0].node.props.onClick();
    nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node.props.submit();
    await new Promise(setImmediate);tree=view.render();
    assert.equal(view.writes.length,1);
    if (fail) {
      assert.equal(nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь').length,1);
      assert.equal(nodes(tree,n=>n.props['aria-label']==='Дата рождения')[0].node.props.value,'29.02');
      assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children?.props?.children==='Пользователь')[0].node.props['aria-pressed'],true);
      assert.equal(nodes(tree,n=>n.props.role==='alert'&&n.props.children==='profile unavailable').length,0);
      assert.equal(view.notifications.filter(n=>n.state==='error'&&n.message==='profile unavailable').length,1);
    } else {
      assert.equal(nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь').length,0);
      nodes(tree,n=>n.type==='button'&&n.props.children==='Сохранить')[0].node.props.onClick();
      await new Promise(setImmediate);
      assert.equal(view.writes[1].accounts[0].role,'user');
      assert.equal(view.writes[1].accounts[0].telegram_id,77);
    }
  }
});

test('cancelling the unified user editor discards profile and access changes',async()=>{
  const guest={telegram_id:77,name:'Visitor',username:'visitor',seen_at:1000,birthdate:null};
  const view=mount(fixture(),[],{contacts:[guest]});
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  view.render();await new Promise(setImmediate);
  const open=()=>nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Visitor')[0].node.props.onClick();
  open();
  nodes(view.render(),n=>n.props['aria-label']==='Дата рождения')[0].node.props.onChange({target:{value:'29.02'}});
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.props?.children==='Пользователь')[0].node.props.onClick();
  nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node.props.close();
  open();
  assert.equal(nodes(view.render(),n=>n.props['aria-label']==='Дата рождения')[0].node.props.value,'');
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children?.props?.children==='Гость')[0].node.props['aria-pressed'],true);
  assert.equal(view.writes.length,0);
});

test('observed guests can be blocked, unblocked and deleted without creating an account',async()=>{
  const guest={telegram_id:77,name:'Visitor',username:'visitor',seen_at:1000,blocked:false};
  const view=mount(fixture(),[],{contacts:[guest],contact:(_path,options)=>({...guest,...(options.body?JSON.parse(options.body):{})})});
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Visitor')[0].node.props.onClick();
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Заблокировать')[0].node.props.onClick();
  await new Promise(setImmediate);
  assert.equal(view.writes[0].blocked,true);
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Разблокировать')[0].node.props.onClick();
  await new Promise(setImmediate);
  assert.equal(view.writes[1].blocked,false);
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Удалить пользователя')[0].node.props.onClick();
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Подтвердить удаление')[0].node.props.onClick();
  await new Promise(setImmediate);
  assert.equal(view.writes[2].method,'DELETE');
  assert.equal(nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Visitor').length,0);
});

test('blocking and deleting are available in user editors for every Telegram role',async()=>{
  for(const role of ['owner','administrator','moderator','user','guest']) {
    const status=fixture();status.settings.accounts=[{telegram_id:11,name:'Role fixture',role,products:role==='guest'?[]:['assistant','mihomo'],excluded_dialogs:[],permission_overrides:{}}];
    const view=mount(status);view.render();await new Promise(setImmediate);
    nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
    view.render();await new Promise(setImmediate);
    nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Role fixture')[0].node.props.onClick();
    const tree=view.render();
    assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Заблокировать')[0].disabled,false);
    assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Удалить пользователя').length,1);
    assert.equal(nodes(tree,n=>n.props.className==='noderaUserActions').length,1);
  }
});

test('a failed guest mutation preserves the profile and reports the error',async()=>{
  const guest={telegram_id:77,name:'Visitor',username:'visitor',seen_at:1000};
  const view=mount(fixture(),[],{contacts:[guest],contact:()=>{throw new Error('temporary failure');}});
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  view.render();await new Promise(setImmediate);
  nodes(view.render(),n=>n.props['aria-label']==='Настроить пользователя Visitor')[0].node.props.onClick();
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Заблокировать')[0].node.props.onClick();
  await new Promise(setImmediate);
  assert.equal(nodes(view.render(),n=>n.props.role==='alert'&&n.props.children==='temporary failure').length,0);
  assert.equal(view.notifications.filter(n=>n.state==='error'&&n.message==='temporary failure').length,1);
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children==='Заблокировать').length,1);
});

test('the empty chat server suggestion runs a direct shortcut instead of preparing an AI question',async()=>{
  const view=mount(fixture(),[],{commands:shortcuts});view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='button'&&n.props.children==='Состояние сервера')[0].node.props.onClick();
  await new Promise(setImmediate);tree=view.render();
  assert.equal(view.writes[0].command,'/status');assert.equal(view.writes[0].request_id,undefined);
});

test('hot commands run without a selected model even while another panel operation is busy',async()=>{
  const view=mount(fixture(),[],{commands:shortcuts,busy:true});
  view.render();await Promise.resolve();let tree=view.render();
  const button=nodes(tree,n=>n.props['aria-label']==='Команды')[0];assert.equal(button.disabled,false);button.node.props.onClick();
  tree=view.render();nodes(tree,n=>n.props.role==='option'&&JSON.stringify(n.props.children).includes('/status'))[0].node.props.onClick();
  await new Promise(setImmediate);tree=view.render();
  assert.equal(view.writes.length,1);assert.equal(view.writes[0].command,'/status');assert.equal(view.writes[0].request_id,undefined);
  assert.match(JSON.stringify(tree),/Command output/);
});

test('slash autocomplete filters commands and Enter executes the chosen shortcut once',async()=>{
  const view=mount(fixture(),[],{commands:shortcuts});view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/sta',style:{},scrollHeight:48}});tree=view.render();
  assert.equal(nodes(tree,n=>n.props.role==='option').length,1);
  nodes(tree,n=>n.type==='textarea')[0].node.props.onKeyDown({key:'Enter',preventDefault(){}});
  await new Promise(setImmediate);tree=view.render();assert.equal(view.writes.length,1);assert.equal(view.writes[0].command,'/status');
});

test('an exact service command opens its arguments instead of running services and trailing space closes autocomplete',async()=>{
  const view=mount(fixture(),[],{commands:shortcuts});view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/service',style:{},scrollHeight:48}});tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onKeyDown({key:'Enter',preventDefault(){}});tree=view.render();
  assert.equal(nodes(tree,n=>n.type==='textarea')[0].node.props.value,'/service ');assert.equal(view.writes.length,0);
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/service ',style:{},scrollHeight:48}});tree=view.render();
  assert.equal(nodes(tree,n=>n.props.role==='listbox'&&n.props['aria-label']==='Быстрые команды').length,0);
});

test('a shortcut requiring arguments fills the composer and service preparation keeps confirmation cards',async()=>{
  const pending={code:'aabbccdd',service:'mihomo',action:'restart',expires:Date.now()/1000+600};
  const view=mount(fixture(),[],{commands:shortcuts,command:()=>({text:'Confirm the action',pending_actions:[pending],context_safe:true})});
  view.render();await Promise.resolve();let tree=view.render();nodes(tree,n=>n.props['aria-label']==='Команды')[0].node.props.onClick();tree=view.render();
  nodes(tree,n=>n.props.role==='option'&&JSON.stringify(n.props.children).includes('/service <ID>'))[0].node.props.onClick();tree=view.render();
  assert.equal(nodes(tree,n=>n.type==='textarea')[0].node.props.value,'/service ');assert.equal(view.writes.length,0);
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/service mihomo restart',style:{},scrollHeight:48}});tree=view.render();
  const send=nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0];assert.equal(send.disabled,false);send.node.props.onClick();
  await new Promise(setImmediate);tree=view.render();assert.equal(view.writes[0].command,'/service mihomo restart');
  assert.equal(nodes(tree,n=>n.props.role==='region'&&n.props['aria-label']==='Подтверждение действий').length,1);
});

test('AI-prepared operations expose human decisions in the conversation',async()=>{
  const status=fixture();Object.assign(status.settings,{model:'fixture-model',modules:{'ai-agent':true,'nodera-bot':true}});Object.assign(status.modules[0],{enabled:true,configured:true});
  const pending={code:'aabbccdd',operation_id:'aabbccdd'.repeat(4),service:'Mihomo /routing/settings',action:'PATCH',expires:Date.now()/1000+600};
  const view=mount(status,[],{chat:()=>({result:{answer:'Review routing changes',tools_used:['mihomo_settings']},pending_actions:[pending]})});
  view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'Prepare routing changes',style:{},scrollHeight:48}});tree=view.render();
  nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();await new Promise(setImmediate);tree=view.render();
  const transcript=nodes(tree,n=>n.props.role==='log')[0].node;
  for(const label of ['Подтвердить','Отказать'])assert.equal(nodes(transcript,n=>n.type==='button'&&n.props.children===label).length,1);
  const answer=nodes(transcript,n=>n.type==='article'&&n.props.className?.split(' ').includes('mcpAnswer'))[0].node;
  assert.equal(nodes(answer,n=>n.type==='button'&&n.props.children==='Подтвердить').length,1);
  assert.equal(nodes(tree,n=>n.props.role==='dialog').length,0);
  assert.equal(nodes(tree,n=>n.props.className==='mcpQuickCommands').length,0);
});

test('the ai shortcut strips only its prefix and uses the normal AI job pipeline',async()=>{
  const status=fixture();Object.assign(status.settings,{model:'fixture-model',modules:{'ai-agent':true,'nodera-bot':true}});Object.assign(status.modules[0],{enabled:true,configured:true});
  const view=mount(status,[],{commands:shortcuts});view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/ai Explain this',style:{},scrollHeight:48}});tree=view.render();
  nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();await new Promise(setImmediate);view.render();
  assert.equal(view.writes[0].prompt,'Explain this');assert.equal(view.writes[0].command,undefined);assert.equal(view.writes[0].model,'fixture-model');
});

test('an unknown slash command stays out of the AI pipeline and reports its server rejection',async()=>{
  const view=mount(fixture(),[],{commands:shortcuts,command:()=>{throw new Error('Unknown command');}});view.render();await Promise.resolve();let tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'/unknown',style:{},scrollHeight:48}});tree=view.render();
  nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();await new Promise(setImmediate);tree=view.render();
  assert.equal(view.writes.length,1);assert.equal(view.writes[0].command,'/unknown');assert.equal(view.writes[0].request_id,undefined);assert.match(JSON.stringify(tree),/Unknown command/);
});
function fixture() {
  return { chat_jobs:true, chat_streaming:true, settings: { revision: 'a'.repeat(32), modules: { 'ai-agent': false, 'nodera-bot': true },
    mcp: { 'telegram-mcp': false, 'vps-controller-mcp': false }, provider_url: '', model: '', telegram_api_id: null,
    accounts: [], api_key_configured: false, bot_token_configured: true, telegram_api_hash_configured: false },
    modules: ['ai-agent', 'nodera-bot'].map(id => ({ id, installed: true, configured: id === 'nodera-bot', management: { state: 'managed' } })),
    mcp_modules: [], tools: {}, bot: { active: true }, legacy_bot: {} };
}

test('a browser reload restores actor, model, draft, streaming and transcript even with server history disabled',async()=>{
  const previous=globalThis.sessionStorage,entries=new Map();
  globalThis.sessionStorage={getItem:key=>entries.get(key)??null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};
  try{
    const status=fixture();Object.assign(status.settings,{memory_enabled:false,model_mode:'catalog',model:'default-model',modules:{'ai-agent':true,'nodera-bot':true}});Object.assign(status.modules[0],{enabled:true,configured:true});
    status.settings.accounts=[{telegram_id:11,name:'Selected actor',excluded_dialogs:[],bot_access:false,telegram_access:false,vps_access:false,session_configured:false}];
    const client={sessionKey:'reload-fixture'};
    chatSession.rememberActor(client,'11');chatSession.saveChatSession(client,'11',{provider:chatSession.chatProvider(status.settings),model:'chosen-model',streaming:true,conversation_id:null,prompt:'Draft before reload',turns:[{id:4,question:'Earlier question',answer:'Earlier answer',model:'chosen-model',files:[]}]});
    async function restored(){const view=mount(status,[],{sessionKey:client.sessionKey});for(let index=0;index<4;index++){view.render();await new Promise(setImmediate);}return view;}
    const first=await restored();let tree=first.render();
    assert.match(JSON.stringify(tree),/Earlier answer/);assert.match(JSON.stringify(nodes(tree,n=>n.props.className==='noderaChatScope')[0].node),/Selected actor/);
    assert.equal(nodes(tree,n=>n.type==='textarea')[0].node.props.value,'Draft before reload');
    assert.match(JSON.stringify(nodes(tree,n=>n.props['aria-label']==='Модель для запроса')[0].node),/chosen-model/);
    nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'Draft edited before next reload',style:{},scrollHeight:48}});first.render();first.flushSession();
    const second=await restored();tree=second.render();assert.equal(nodes(tree,n=>n.type==='textarea')[0].node.props.value,'Draft edited before next reload');assert.equal(second.writes.length,0);
    nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();await new Promise(setImmediate);second.render();
    assert.equal(second.writes[0].telegram_id,11);assert.equal(second.writes[0].model,'chosen-model');assert.equal(second.writes[0].stream,true);
  }finally{globalThis.sessionStorage=previous;}
});

test('restoring a running job reads its saved result once without resubmitting generation',async()=>{
  const previous=globalThis.sessionStorage,entries=new Map();globalThis.sessionStorage={getItem:k=>entries.get(k)??null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k)};
  try{
    const status=fixture();Object.assign(status.settings,{memory_enabled:false,model:'m',modules:{'ai-agent':true,'nodera-bot':true}});Object.assign(status.modules[0],{enabled:true,configured:true});
    const identity='b'.repeat(32),client={sessionKey:'restored-job'};
    chatSession.saveChatSession(client,'',{provider:chatSession.chatProvider(status.settings),model:'m',streaming:false,conversation_id:null,prompt:'',turns:[{id:1,question:'Old',answer:'Previous',model:'m',files:[]},{id:2,question:'In progress',model:'m',files:[],queue_state:'running'}]});
    chatJobs.rememberChat(client,'',identity);
    const jobs=new Map([[identity,Promise.resolve({id:identity,seq:1,state:'done',stage:'complete',question:'In progress',model:'m',stream:false,output:{result:{answer:'Recovered result',tools_used:[]}}})]]);
    const view=mount(status,[],{sessionKey:client.sessionKey,jobs});for(let index=0;index<5;index++){view.render();await new Promise(setImmediate);}
    const tree=view.render();assert.equal(view.writes.length,0);assert.match(JSON.stringify(tree),/Recovered result/);
    assert.equal(nodes(tree,n=>n.props.className==='mcpUserMessage'&&n.props.children.includes('In progress')).length,1);
  }finally{globalThis.sessionStorage=previous;}
});

test('server history restores the selected conversation without overwriting a newer draft, model or command output',async()=>{
  const previous=globalThis.sessionStorage,entries=new Map();globalThis.sessionStorage={getItem:k=>entries.get(k)??null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k)};
  try{
    const status=fixture();Object.assign(status.settings,{model_mode:'catalog',model:'default-model'});
    const client={sessionKey:'history-restoration'},identity='c'.repeat(32),reads=[];
    const turn={id:1,question:'Saved question',answer:'Saved answer',model:'old-model',files:[]};
    chatSession.saveChatSession(client,'',{provider:chatSession.chatProvider(status.settings),model:'chosen-model',streaming:false,conversation_id:identity,prompt:'Keep my draft',turns:[turn,{id:2,question:'/status',answer:'Command output',model:'',context_safe:true,files:[]}]});
    const view=mount(status,[],{sessionKey:client.sessionKey,conversations:path=>{reads.push(path);return path.split('?')[0].endsWith(identity)?{id:identity,turns:[turn]}:{enabled:true,items:[{id:'d'.repeat(32),title:'Newer different conversation'},{id:identity,title:'Selected conversation'}]};}});
    for(let index=0;index<6;index++){view.render();await new Promise(setImmediate);}
    const tree=view.render();assert(reads.some(path=>path.endsWith(identity)));assert(!reads.some(path=>path.endsWith('d'.repeat(32))));
    assert.equal(nodes(tree,n=>n.type==='textarea')[0].node.props.value,'Keep my draft');assert.match(JSON.stringify(tree),/Command output/);
    assert.match(JSON.stringify(nodes(tree,n=>n.props['aria-label']==='Модель для запроса')[0].node),/chosen-model/);
  }finally{globalThis.sessionStorage=previous;}
});

test('saved connection data is visible in AI settings before a manual check and unavailable fields are omitted', async () => {
  const status = fixture(), identity = 'b'.repeat(32);
  status.settings.providers = [{ id: identity, name: 'Fixture API', provider_url: 'https://provider.example/v1', model: '', model_mode: 'catalog', api_key_configured: true }];
  status.settings.active_provider = identity;
  status.provider_observations = { [identity]: { quota: { unlimited: true, remaining: -50, used: 0, expires_at: 0 }, quota_checked_at: 1700000000,
    provider_info: { checked_at: 1700000000, catalog_latency_ms: 123, models_count: 43 } } };
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  const text = JSON.stringify(tree);
  assert.match(text, /Без лимита/);
  assert.match(text, /Моделей: /);
  nodes(tree, n => n.props['aria-label'] === 'Настроить Fixture API')[0].node.props.onClick();
  tree = view.render();
  assert.match(JSON.stringify(tree), /Без срока/);
  assert.match(JSON.stringify(tree), /Использовано/);
  assert.doesNotMatch(text, /Аккаунт ·/);
});

test('one pending request renders one preparing status without a duplicate or a false queue', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'fixture-model', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  let resolve;
  const view = mount(status, [], {chat: () => new Promise(done => resolve = done), queue: () => ({state:'queued', position:2, waiting:2})});
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:'hello',style:{},scrollHeight:48}});
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].node.props.onClick();
  view.render(); await new Promise(setImmediate); tree = view.render();
  const pending = nodes(tree, n => n.props.role === 'status');
  assert.equal(pending.length, 1);
  assert.match(JSON.stringify(pending[0].node.props.children), /Подготавливаю запрос/);
  assert.match(view.writes[0].request_id, /^[0-9a-f]{32}$/);
  resolve({result:{answer:'finished', tools_used:[]}});
  await new Promise(setImmediate); tree = view.render();
  assert.equal(view.writes.length, 1);
  assert.equal(nodes(tree, n => n.props.role === 'status').length, 0);
});

test('pending turn survives inactive page and answer notification opens chat without another POST', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'fixture-model', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  let resolve, opened = 0;
  const view = mount(status, [], {chat: () => new Promise(done => resolve = done), onOpenChat: () => opened++});
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:'pending question',style:{},scrollHeight:48}});
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].node.props.onClick();
  view.setActive(false); tree = view.render();
  assert.match(JSON.stringify(tree), /pending question/);
  assert.equal(nodes(tree, n => n.type === 'textarea')[0].disabled, false);
  view.setActive(true); tree = view.render();
  assert.match(JSON.stringify(tree), /pending question/);
  view.setActive(false); view.render();
  await new Promise(setImmediate);
  resolve({result:{answer:'completed answer', tools_used:[]}});
  await new Promise(setImmediate);
  assert.equal(view.writes.length, 1);
  const notice = view.notifications.find(item => item.action);
  assert.ok(notice);
  notice.action.run();
  assert.equal(opened, 1);
  view.setActive(true); tree = view.render();
  assert.equal(view.notifications.filter(item => item.id?.startsWith('nodera-answer:')).length, 0);
  assert.match(JSON.stringify(tree), /completed answer/);
});

test('follow-ups are durably admitted with predecessor IDs and queued messages can be removed', async () => {
  const status = fixture();
  status.chat_attachments = true;
  Object.assign(status.settings, {model:'fixture-model', memory_enabled:false, modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  const replies = [];
  const view = mount(status, [], {chat: () => new Promise(resolve => replies.push(resolve))});
  view.render(); await Promise.resolve();
  function submit(question) {
    let tree = view.render();
    nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:question,style:{},scrollHeight:48}});
    tree = view.render();
    const send = nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0];
    assert.equal(send.disabled, false);
    send.node.props.onClick();
  }
  submit('first'); submit('second'); submit('remove me'); submit('third');
  await new Promise(setImmediate);
  assert.equal(view.writes.length, 4);
  let tree = view.render();
  const removals = nodes(tree, n => n.type === 'button' && n.props.children === 'Отменить запрос');
  assert.equal(removals.length, 4);
  await removals[2].node.props.onClick();
  assert.doesNotMatch(JSON.stringify(view.render()), /remove me/);
  view.setActive(false); view.render();
  replies[0]({result:{answer:'first answer',tools_used:[]},conversation_id:'conversation-one'});
  await new Promise(setImmediate);
  assert.equal(view.writes.length, 4);
  assert.equal(view.writes[1].prompt, 'second');
  assert.equal(view.writes[1].previous_request_id, view.writes[0].request_id);
  assert.deepEqual(view.writes[1].history, []);
  replies[1]({result:{answer:'second answer',tools_used:[]},conversation_id:'conversation-one'});
  await new Promise(setImmediate);
  assert.equal(view.writes.length, 4);
  assert.equal(view.writes[3].prompt, 'third');
  assert.equal(view.writes[3].previous_request_id, view.writes[2].request_id);
  assert.equal(new Set(view.writes.map(item => item.request_id)).size, 4);
  replies[3]({result:{answer:'third answer',tools_used:[]}});
  await new Promise(setImmediate);
  view.setActive(true); tree = view.render();
  assert.match(JSON.stringify(tree), /third answer/);
  assert.equal(nodes(tree, n => n.type === 'button' && n.props.children === 'Отменить запрос').length, 0);
});

test('confirmation preserves follow-ups on the server queue and keeps the decision card', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'fixture-model', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  const replies=[];
  const view = mount(status, [], {chat: () => new Promise(done => replies.push(done))});
  view.render(); await Promise.resolve();
  for (const question of ['action','follow-up']) {
    let tree = view.render();
    nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:question,style:{},scrollHeight:48}});
    tree = view.render();
    nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].node.props.onClick();
  }
  await new Promise(setImmediate);
  replies[0]({result:{answer:'confirmation needed',tools_used:[]},pending_action:{service:'ssh',action:'restart',code:'pending',expires:9999999999}});
  await new Promise(setImmediate);
  const tree = view.render();
  assert.equal(view.writes.length, 2);
  const confirmation = nodes(tree, n => n.props.role === 'region')[0].node;
  assert.equal(nodes(confirmation, n => n.type === 'button' && n.props.children === 'Подтвердить').length, 1);
  assert.equal(nodes(confirmation, n => n.type === 'button' && n.props.children === 'Отказать').length, 1);
  assert.equal(nodes(tree, n => n.type === 'button' && n.props.children === 'Повторить').length, 0);
  replies[1]({result:{answer:'follow-up answer',tools_used:[]}}); await new Promise(setImmediate);
});

test('incomplete chat retains its partial answer and the queued question receives an answer', async () => {
  const status=fixture();Object.assign(status.settings,{model:'fixture-model',modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0],{enabled:true,configured:true});
  const finish=[];
  const view=mount(status,[],{chat:()=>new Promise(resolve=>{finish.push(resolve);})});
  view.render();await Promise.resolve();
  for(const question of ['audit','follow-up']) {
    nodes(view.render(),n=>n.type==='textarea')[0].node.props.onChange({target:{value:question,style:{},scrollHeight:48}});
    nodes(view.render(),n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();
  }
  await new Promise(setImmediate);
  finish[0]({result:{answer:'Only part of the audit is available',tools_used:['mihomo_read'],status:'incomplete'}});
  await new Promise(setImmediate);
  assert.equal(view.writes.length,2);
  assert.match(JSON.stringify(view.render()),/Only part of the audit is available/);
  assert.doesNotMatch(JSON.stringify(view.render()),/Не отправлено/);
  finish[1]({result:{answer:'follow-up processed',tools_used:[]}}); await new Promise(setImmediate);
  assert.match(JSON.stringify(view.render()),/follow-up processed/);
});

test('failed chat does not discard accepted follow-ups or repeat a generation POST', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'fixture-model', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  const rejects=[],replies=[];
  const view = mount(status, [], {chat: () => new Promise((done, fail) => {replies.push(done);rejects.push(fail);})});
  view.render(); await Promise.resolve();
  for (const question of ['first','follow-up']) {
    let tree = view.render();
    nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:question,style:{},scrollHeight:48}});
    tree = view.render();
    nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].node.props.onClick();
  }
  await new Promise(setImmediate);
  rejects[0](new Error('connection lost'));
  await new Promise(setImmediate);
  const tree = view.render();
  assert.equal(view.writes.length, 2);
  assert.match(JSON.stringify(tree), /follow-up/);
  assert.equal(nodes(tree, n => n.type === 'button' && n.props.children === 'Повторить').length, 1);
  assert.equal(nodes(tree, n => n.type === 'textarea')[0].disabled, false);
  replies[1]({result:{answer:'second request completed',tools_used:[]}}); await new Promise(setImmediate);
  assert.match(JSON.stringify(view.render()),/second request completed/);
});

test('all MCP cards show their actual tool counts', async () => {
  const status = fixture();
  status.mcp_modules = [{id:'telegram-mcp', name:'Telegram MCP'}, {id:'vps-controller-mcp', name:'VPS Controller'}, {id:'mihomo-mcp', name:'Mihomo'}];
  status.tools = {'telegram-mcp': Array.from({length:54}, (_,i) => `tg_${i}`), 'vps-controller-mcp': Array.from({length:17}, (_,i) => `vps_${i}`), 'mihomo-mcp': ['a','b','c','d','e']};
  const view = mount(status); view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  const tree = view.render();
  for (const [name, count] of [['Telegram MCP',54],['VPS Controller',17],['Mihomo',5]]) {
    const item = nodes(tree, n => n.props['aria-label'] === `Количество инструментов ${name}`)[0].node;
    assert.equal(item.props.children.includes(count), true);
  }
});

test('catalog failure is visible and keeps chat available with the saved model; checking restores status', async () => {
  const status = fixture();
  Object.assign(status.settings, {model_mode:'catalog', model:'saved-model', api_key_configured:true,
    active_provider:'a'.repeat(32), provider_url:'https://provider.example/v1', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  let requests = 0;
  const view = mount(status, [], {models: async options => {
    assert.equal(JSON.parse(options.body).provider_id, status.settings.active_provider);
    if (++requests === 1) throw new Error('temporary outage');
    return {models:['saved-model','new-model']};
  }});
  view.render(); await Promise.resolve(); view.render(); await new Promise(setImmediate);
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'alert' && n.props.children === 'temporary outage').length, 0);
  assert.equal(view.notifications.filter(n => n.message === 'temporary outage').length, 1);
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:'hello', style:{}, scrollHeight:48}});
  tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].disabled, false);
  view.notifications.find(n => n.message === 'temporary outage').action.run();
  await new Promise(setImmediate); tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'img' && n.props['aria-label'] === 'Каталог API доступен').length, 1);
  assert.equal(view.notifications.filter(n => n.message === 'temporary outage').length, 0);
});

test('Telegram exposes all 54 switches per user and saves individual tools with allowed targets', async () => {
  const status = fixture();
  status.settings.telegram_tool_catalog = JSON.parse(readFileSync(new URL('../../protocol-images/ai-agent/telegram_catalog.json', import.meta.url), 'utf8'));
  status.settings.accounts = [{ telegram_id: 11, name: 'Fixture', bot_access: false, bot_chat_access: true,
    telegram_access: true, vps_access: false, session_configured: true, excluded_dialogs: [99] }];
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  let tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить пользователя Fixture')[0].node.props.onClick();
  tree = view.render();
  const tools = nodes(tree, n => n.type === 'label' && n.props.title?.startsWith('tg_'));
  assert.equal(tools.length, 54);
  assert.equal(tools.filter(({node}) => nodes(node, n => n.type === 'input' && n.props.checked).length).length, 5);
  const send = tools.find(({node}) => node.props.title === 'tg_send_message').node;
  nodes(send, n => n.type === 'input')[0].node.props.onChange({target: {checked: true}});
  tree = view.render();
  assert.equal(nodes(nodes(tree, n => n.props.title === 'tg_send_message')[0].node, n => n.type === 'input')[0].node.props.checked, true);
  nodes(tree, n => n.type === 'input' && n.props.placeholder?.startsWith('ID, username'))[0].node.props.onChange({target: {value: '42, @example'}});
  tree = view.render();
  nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Пользователь')[0].node.props.submit();
  tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(view.writes[0].accounts[0].telegram_tools.includes('tg_send_message'), true);
  assert.equal(view.writes[0].accounts[0].telegram_tools.includes('tg_delete_messages'), false);
  assert.deepEqual(view.writes[0].accounts[0].telegram_action_targets, ['42', '@example']);
  assert.deepEqual(view.writes[0].accounts[0].excluded_dialogs, [99]);
});

test('Mihomo and VPS have separate settings cards and saving one scope leaves the other disabled', async () => {
  const status = fixture();
  status.settings.mcp = { 'telegram-mcp': false, 'vps-controller-mcp': false, 'mihomo-mcp': false };
  status.mcp_modules = [
    { id: 'telegram-mcp', name: 'Telegram MCP', enabled: false, configured: true },
    { id: 'vps-controller-mcp', name: 'VPS Controller', enabled: false, configured: true },
    { id: 'mihomo-mcp', name: 'Mihomo', enabled: false, configured: true },
  ];
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Mihomo · Выключен')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'VPS Controller · Выключен').length, 1);
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'Mihomo · Включён').length, 1);
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(view.writes[0].mcp['mihomo-mcp'], true);
  assert.equal(view.writes[0].mcp['vps-controller-mcp'], false);
});

test('AI settings can be edited and submitted while NoderaBot is running', async () => {
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'h1').length, 0);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  const toggle = nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'AI')[0];
  assert.equal(toggle.disabled, false);
  toggle.node.props.onClick();
  tree = view.render();
  const save = nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0];
  assert.equal(save.disabled, false);
  save.node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].component, 'nodera');
  assert.equal(view.writes[0].modules['ai-agent'], true);
  assert.equal(view.writes[0].bot_token, null);
  assert.ok(view.notifications.some(item => item.state === 'success' && item.message === 'Настройки сохранены'));
});

test('access can save role permissions during an AI response without mixing chat and save lifetimes', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'m', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true,configured:true});
  let finishChat, finishSave;
  const view = mount(status, [], {
    chat: () => new Promise(resolve => { finishChat = resolve; }),
    save: () => new Promise(resolve => { finishSave = resolve; }),
  });
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'hello',style:{},scrollHeight:48}});
  nodes(view.render(),n=>n.type==='textarea')[0].node.props.onKeyDown({key:'Enter',preventDefault(){}});
  await new Promise(setImmediate);
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Настроить роли')[0].node.props.onClick();
  tree=view.render();
  const permission=nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0];
  assert.equal(permission.disabled,false);
  permission.node.props.onChange({target:{checked:false}});
  tree=view.render();
  nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Роли и права')[0].node.props.submit();
  tree=view.render();
  const save=nodes(tree,n=>n.type==='button'&&n.props.children==='Сохранить')[0];
  assert.equal(save.disabled,false);
  save.node.props.onClick(); await Promise.resolve();
  assert.equal(view.writes.length,2);
  assert.ok(!view.writes[1].role_permissions.user.includes('web'));
  finishChat({result:{answer:'Completed while saving',tools_used:[]}});
  await new Promise(setImmediate);
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить')[0].disabled,true);
  finishSave({...status,settings:{...status.settings,revision:'b'.repeat(32),role_permissions:view.writes[1].role_permissions}});
  await new Promise(setImmediate);
  tree=view.render();
  assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Сохранить').length,0);
  nodes(tree,n=>n.type==='button'&&n.props.children?.[1]==='Чат')[0].node.props.onClick();
  assert.equal(nodes(view.render(),n=>n.props.text==='Completed while saving').length,1);
  assert.equal(view.writes.filter(value=>value.prompt==='hello').length,1);
});

test('running Telegram bot allows token editing and shows the stop action', async () => {
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  const tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'fieldset' && n.props.className === 'noderaBotToken')[0].disabled, false);
  const botSwitch = nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Telegram bot')[0];
  assert.equal(botSwitch.disabled, false);
  assert.equal(botSwitch.node.props['aria-checked'], true);
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'AI').length, 0);
});

test('background panel work does not block role or user editing while active bot settings are preserved', async () => {
  const status=fixture();status.settings.modules['ai-agent']=true;
  status.settings.accounts=[{telegram_id:11,name:'Member',role:'user',products:['assistant'],excluded_dialogs:[],permission_overrides:{},session_configured:false}];
  const view=mount(status,[],{busy:true});view.render();await Promise.resolve();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  let tree=view.render();
  const open=nodes(tree,n=>n.type==='button'&&n.props.children?.[1]==='Настроить роли')[0];
  assert.equal(open.disabled,false);open.node.props.onClick();
  tree=view.render();
  const permission=nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0];
  assert.equal(permission.disabled,false);permission.node.props.onChange({target:{checked:false}});
  nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Роли и права')[0].node.props.submit();
  tree=view.render();
  const user=nodes(tree,n=>n.props['aria-label']==='Настроить пользователя Member')[0];
  assert.equal(user.disabled,false);user.node.props.onClick();
  tree=view.render();nodes(tree,n=>n.type==='input'&&n.props.value==='Member')[0].node.props.onChange({target:{value:'Edited while busy'}});
  nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node.props.submit();
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить')[0].disabled,true);
  view.setBusy(false);
  const save=nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить')[0];
  assert.equal(save.disabled,false);save.node.props.onClick();await Promise.resolve();
  assert.equal(view.writes[0].accounts[0].name,'Edited while busy');
  assert.ok(!view.writes[0].role_permissions.user.includes('web'));
  assert.equal(view.writes[0].modules['nodera-bot'],true);
});

test('chat scope lives in settings and subscription binding is saved with address preferences', async () => {
  const status = fixture();
  status.settings.accounts = [{ telegram_id: 11, name: 'User', bot_chat_access: true, bot_access: false,
    telegram_access: false, vps_access: false, excluded_dialogs: [], session_configured: false }];
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Контур').length, 0);
  const gear = nodes(tree, n => n.props['aria-label'] === 'Настройки')[0].node;
  assert.equal(gear.props.children.includes('Настройки'), false);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки чата')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Контур').length, 1);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  view.render(); await Promise.resolve();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить пользователя User')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Подписка Mihomo')[0].node.props.onChange({ target: { value: 'own' } });
  tree = view.render();
  nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Форма обращения')[0].node.props.onChange({ target: { value: 'male' } });
  tree = view.render();
  nodes(tree, n => n.props.title === 'Пользователь' && typeof n.props.submit === 'function')[0].node.props.submit();
  tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes[0].accounts[0].mihomo_profile_id, 'own');
  assert.equal(view.writes[0].accounts[0].address_gender, 'male');
});

test('catalog mode loads any number of models and sends the chat selection without changing settings', async () => {
  const status = fixture();
  Object.assign(status.settings, { model_mode: 'catalog', model: '', api_key_configured: true,
    provider_url: 'https://api.nyxos.shop/v1', modules: { 'ai-agent': true, 'nodera-bot': true } });
  Object.assign(status.modules[0], { enabled: true, configured: true });
  const models = Array.from({ length: 250 }, (_, i) => `model-${i}`);
  const view = mount(status, models);
  view.render(); await Promise.resolve(); view.render(); await new Promise(setImmediate);
  let tree = view.render();
  const selector = nodes(tree, n => n.type === 'button' && n.props['aria-label'] === 'Модель для запроса')[0];
  assert.equal(selector.node.props['aria-expanded'], false);
  assert.equal(selector.disabled, false);
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', ctrlKey: true, preventDefault() {} });
  assert.equal(view.writes.length, 0);
  selector.node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'input' && n.props['aria-label'] === 'Найти модель').length, 0);
  const options = nodes(tree, n => n.props.role === 'option');
  assert.equal(options.length, 250);
  options.at(-1).node.props.onClick();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', ctrlKey: true, preventDefault() {} });
  await Promise.resolve();
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].model, 'model-249');
  assert.equal(status.settings.model, '');
});

test('new catalog chats use the saved default and copy actions sit outside the answer', async () => {
  const status = fixture();
  Object.assign(status.settings, { model_mode: 'catalog', model: 'default-model', api_key_configured: true,
    provider_url: 'https://provider.example/v1', modules: { 'ai-agent': true, 'nodera-bot': true } });
  Object.assign(status.modules[0], { enabled: true, configured: true });
  const view = mount(status, ['default-model', 'another-model']);
  view.render(); await Promise.resolve(); view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(view.writes[0].model, 'default-model');
  tree = view.render();
  const answer = nodes(tree, n => n.type === 'article' && n.props.className.startsWith('mcpAnswer'))[0].node;
  assert.equal(nodes(answer, n => n.props['aria-label'] === 'Скопировать ответ').length, 0);
  const actions = nodes(tree, n => n.props.className === 'mcpAnswerActions')[0].node;
  assert.equal(nodes(actions, n => n.props['aria-label'] === 'Скопировать ответ').length, 1);
  nodes(tree, n => n.props['aria-label'] === 'Модель для запроса')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props.role === 'option').at(-1).node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Новый чат')[0].node.props.onClick();
  tree = view.render();
  const selection = nodes(tree, n => n.props['aria-label'] === 'Модель для запроса')[0].node;
  assert.equal(selection.props.children[0].props.children, 'default-model');
});

test('user permission switch can override and restore role inheritance while sections keep account data', async () => {
  const status=fixture();
  status.settings.accounts=[{telegram_id:11,name:'Member',role:'user',products:['assistant'],excluded_dialogs:[],permission_overrides:{},session_configured:false}];
  const view=mount(status);view.render();await Promise.resolve();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  let tree=view.render();
  nodes(tree,n=>n.props['aria-label']==='Настроить пользователя Member')[0].node.props.onClick();
  const userTree=()=>nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node;
  tree=userTree();
  nodes(tree,n=>n.type==='input'&&n.props.value==='Member')[0].node.props.onChange({target:{value:'Member updated'}});
  tree=userTree();
  nodes(tree,n=>n.type==='button'&&n.props.children==='Личные права')[0].node.props.onClick();
  tree=userTree();
  const permission=nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0];
  assert.equal(permission.node.props.checked,true);
  permission.node.props.onChange({target:{checked:false}});
  tree=userTree();
  assert.equal(nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0].node.props.checked,false);
  nodes(tree,n=>n.props['aria-label']==='По роли: Поиск в интернете')[0].node.props.onClick();
  tree=userTree();
  assert.equal(nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0].node.props.checked,true);
  nodes(tree,n=>typeof n.type==='function'&&n.props.selected&&n.props.select)[0].node.props.select('MCP Telegram');
  tree=userTree();
  nodes(tree,n=>n.props.role==='switch'&&n.props['aria-label']==='Доступ к MCP Telegram')[0].node.props.onChange({target:{checked:false}});
  tree=userTree();
  assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Telegram API').length,0);
  nodes(tree,n=>n.props['aria-label']==='По роли: Доступ к MCP Telegram')[0].node.props.onClick();
  tree=userTree();
  assert.equal(nodes(tree,n=>n.type==='button'&&n.props.children==='Telegram API').length,1);
  nodes(tree,n=>n.type==='button'&&n.props.children==='Telegram API')[0].node.props.onClick();
  tree=view.render();
  const dialog=nodes(tree,n=>typeof n.type==='function'&&n.props.title==='Пользователь')[0].node;
  dialog.props.submit();
  tree=view.render();
  nodes(tree,n=>n.type==='button'&&n.props.children==='Сохранить')[0].node.props.onClick();
  await Promise.resolve();
  assert.deepEqual(view.writes.at(-1).accounts[0].permission_overrides,{});
  assert.equal(view.writes.at(-1).accounts[0].telegram_id,11);
  assert.equal(view.writes.at(-1).accounts[0].name,'Member updated');
});

test('tools use switches and gears; model and tool count are separate header facts', async () => {
  const status = fixture();
  status.web_tools = true; status.context_tools = true;
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Интернет').length, 1);
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Персонализация').length, 1);
  assert.equal(nodes(tree, n => n.type === 'dt' && n.props.children === 'Модель по умолчанию').length, 1);
  assert.equal(nodes(tree, n => n.type === 'dt' && n.props.children === 'Инструменты').length, 1);
  nodes(tree, n => n.props['aria-label'] === 'Настроить Интернет')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.props.title === 'Интернет' && typeof n.type === 'function').length, 1);
});

test('access exposes all five roles, owner permissions remain fixed, and personalisation is wide and grouped', async () => {
  const status = fixture(); status.context_tools = true;
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label']?.startsWith('Роль: ')).length, 0);
  nodes(tree,n=>n.type==='button'&&n.props.children?.[1]==='Настроить роли')[0].node.props.onClick();
  tree=view.render();
  assert.equal(nodes(tree, n => n.props['aria-label']?.startsWith('Роль: ')).length, 5);
  nodes(tree, n => n.props['aria-label'] === 'Роль: Владелец')[0].node.props.onClick();
  tree = view.render();
  const owner = nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Роли и права')[0].node;
  assert.equal(nodes(owner, n => n.type === 'input' && n.props.type === 'checkbox').filter(item => !item.disabled).length, 0);
  owner.props.close();
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить Персонализация')[0].node.props.onClick();
  tree = view.render();
  const personal = nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Персонализация')[0].node;
  assert.equal(personal.props.wide, true);
  assert.equal(nodes(personal, n => n.type === 'nav' && n.props['aria-label'] === 'Разделы персонализации').length, 1);
  assert.equal(nodes(personal, n => n.type === 'h3' && n.props.children === 'Личная память собеседника').length, 0);
});


test('Telegram alerts open only in a separate Access modal and closing it does not save account settings', async () => {
  const view = mount(fixture()); view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  const alerts = tree => nodes(tree, n => typeof n.type === 'function' && n.type.name === 'TelegramAlerts');
  assert.equal(alerts(view.render()).length, 0);
  nodes(view.render(), n => n.type === 'button' && n.props.children === 'Настроить алерты')[0].node.props.onClick();
  let tree = view.render();
  const dialog = nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Алерты Telegram')[0].node;
  assert.equal(alerts(dialog).length, 1);
  dialog.props.close(); tree = view.render();
  assert.equal(alerts(tree).length, 0);
  assert.equal(view.writes.length, 0);
});

test('role modal stages edits across roles, cancels without changing settings, and applies through the page save', async () => {
  const status=fixture(),view=mount(status);view.render();await Promise.resolve();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  const open=()=>nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Настроить роли')[0].node.props.onClick();
  const dialog=()=>nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Роли и права')[0].node;
  const web=()=>nodes(dialog(),n=>n.props.role==='switch'&&n.props['aria-label']==='Поиск в интернете')[0].node;
  open();web().props.onChange({target:{checked:false}});
  assert.equal(web().props.checked,false);
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить').length,0);
  dialog().props.close();
  assert.equal(nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить').length,0);
  open();assert.equal(web().props.checked,true);web().props.onChange({target:{checked:false}});
  for (const role of ['Модератор','Пользователь']) nodes(dialog(),n=>n.props['aria-label']==='Роль: '+role)[0].node.props.onClick();
  assert.equal(web().props.checked,false);
  dialog().props.submit();
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить')[0].node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes.length,1);
  assert.ok(!view.writes[0].role_permissions.user.includes('web'));
  assert.equal(status.settings.role_permissions,undefined);
});

test('administrator permissions can be edited and saved while ownership stays protected', async () => {
  const view=mount(fixture());view.render();await Promise.resolve();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Доступ')[0].node.props.onClick();
  nodes(view.render(),n=>n.type==='button'&&n.props.children?.[1]==='Настроить роли')[0].node.props.onClick();
  const dialog=()=>nodes(view.render(),n=>typeof n.type==='function'&&n.props.title==='Роли и права')[0].node;
  nodes(dialog(),n=>n.props['aria-label']==='Роль: Администратор')[0].node.props.onClick();
  const search=value=>nodes(dialog(),n=>n.type==='input'&&n.props.type==='search')[0].node.props.onChange({target:{value}});
  search('Терминал');
  const terminal=nodes(dialog(),n=>n.props.role==='switch'&&n.props['aria-label']==='Терминал: только чтение')[0];
  assert.equal(terminal.disabled,false);
  terminal.node.props.onChange({target:{checked:false}});
  search('Управление ролями');
  const ownership=nodes(dialog(),n=>n.props.role==='switch'&&n.props['aria-label']==='Управление ролями и правами')[0];
  assert.equal(ownership.disabled,true);
  dialog().props.submit();
  nodes(view.render(),n=>n.type==='button'&&n.props.children==='Сохранить')[0].node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes.length,1);
  assert.ok(!view.writes[0].role_permissions.administrator.includes('terminal_read'));
  assert.ok(!view.writes[0].role_permissions.administrator.includes('roles_manage'));
});

test('streaming defaults off and can be enabled independently in chat settings', async () => {
  const status=fixture();Object.assign(status.settings,{model:'m',modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0],{enabled:true,configured:true});
  const view=mount(status);view.render();await Promise.resolve();
  let tree=view.render();nodes(tree,n=>n.type==='button'&&n.props.children?.[1]==='Настройки чата')[0].node.props.onClick();
  tree=view.render();const input=nodes(tree,n=>n.type==='input'&&n.props.type==='checkbox')[0];
  assert.equal(input.node.props.checked,false);assert.equal(input.disabled,false);
  input.node.props.onChange({target:{checked:true}});tree=view.render();
  nodes(tree,n=>n.type==='textarea')[0].node.props.onChange({target:{value:'hello',style:{},scrollHeight:48}});
  tree=view.render();nodes(tree,n=>n.props['aria-label']==='Отправить сообщение')[0].node.props.onClick();
  await new Promise(setImmediate);
  assert.equal(view.writes.length,1);assert.equal(view.writes[0].stream,true);
});
