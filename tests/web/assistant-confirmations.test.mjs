import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import {createNotificationStore} from '../../src/shared/notifications/store.ts';
import * as operations from '../../src/control-panel/system-operation.ts';
import {mutationFailureState} from '../../src/shared/lib/api-request.ts';

const action = (code, actor='panel') => ({actor,actor_name:actor,code,expires:Date.now()/1000+600,operation_id:code.repeat(4),action:'restart',service:code});
const plan = (codes, actor='panel', id='f'.repeat(32)) => codes.map(code=>({...action(code,actor),confirmation_request:{request_id:id,title:'Repair connections',reason:'Apply prepared actions in order',operation_ids:codes.map(code=>code.repeat(4))}}));

test('one group button confirms five operations in the presented order and preserves other plans',async()=>{
  const codes=['aabbccdd','11223344','eeff0011','22334455','66778899'];
  const app=mount([...plan(codes).reverse(),...plan(['abcdef01'],'11')]);
  try{
    await app.settle();const cards=app.store.getSnapshot().filter(row=>row.kind==='confirmation');assert.equal(cards.length,2);
    const card=cards.find(row=>row.id.includes('plan:panel:'));
    assert.deepEqual(card.confirmationItems,codes.map(code=>'restart · '+code));
    assert.equal(app.calls.length,0);await card.action.run();await app.settle();
    assert.deepEqual(app.calls.map(row=>row.code),codes);assert(app.calls.every(row=>row.decision==='confirm'&&row.telegram_id===null));
    assert.equal(app.queue.context.length,5);assert.equal(app.queue.actions.length,1);assert.equal(app.queue.actions[0].actor,'11');
  }finally{app.dispose();}
});

test('one rejection cancels all four members without affecting a separate request',async()=>{
  const codes=['aabbccdd','11223344','eeff0011','22334455'],app=mount([...plan(codes),action('66778899')]);
  try{
    await app.settle();await app.store.getSnapshot().find(row=>row.id.includes('plan:panel:')).secondaryAction.run();await app.settle();
    assert.deepEqual(app.calls.map(row=>[row.code,row.decision]),codes.map(code=>[code,'reject']));
    assert.deepEqual(app.queue.actions.map(row=>row.code),['66778899']);
  }finally{app.dispose();}
});

test('group submission stops on a known or uncertain second result and never replays accepted members',async()=>{
  for(const kind of ['http','network']){
    const codes=['aabbccdd','11223344','eeff0011','22334455'];let fail=true;
    const app=mount(plan(codes),{beforeDecision:(_body,index)=>{if(fail&&index===2)throw Object.assign(new Error('Admission interrupted'),{kind,status:409});}});
    try{
      await app.settle();await app.store.getSnapshot().find(row=>row.kind==='confirmation').action.run();await app.settle();
      assert.equal(app.calls.length,2);assert(!app.queue.actions.some(row=>row.code===codes[0]));
      assert.equal(app.queue.actions.some(row=>row.code===codes[1]),kind==='http');
      const remaining=app.queue.actions.map(row=>row.code);fail=false;
      await app.store.getSnapshot().find(row=>row.kind==='confirmation').action.run();await app.settle();
      assert.deepEqual(app.calls.slice(2).map(row=>row.code),remaining);assert.equal(app.calls.filter(row=>row.code===codes[0]).length,1);
    }finally{app.dispose();}
  }
});

test('a group waits for operation success, ignores a concurrent decision and stops on worker failure',async()=>{
  const codes=['aabbccdd','11223344','eeff0011','22334455','66778899'];
  const options={jobs:true,beforeDecision:(_body,index)=>{options.jobState=index===1?'queued':'succeeded';}},app=mount(plan(codes),options);
  try{
    await app.settle();const card=app.store.getSnapshot().find(row=>row.kind==='confirmation');const running=card.action.run();await app.settle();
    assert.equal(app.calls.length,1);await card.action.run();await card.secondaryAction.run();assert.equal(app.calls.length,1);
    app.remote.jobs[codes[0].repeat(4)].state='succeeded';await app.tick();await running;await app.settle();
    assert.deepEqual(app.calls.map(row=>row.code),codes);
  }finally{app.dispose();}
  const failed=mount(plan(codes),{jobs:true,jobState:'failed'});
  try{
    await failed.settle();await failed.store.getSnapshot().find(row=>row.kind==='confirmation').action.run();await failed.settle();
    assert.equal(failed.calls.length,1);assert.equal(failed.queue.actions.length,4);assert(Object.keys(failed.queue.errors).some(key=>key.startsWith('plan:')));
  }finally{failed.dispose();}
});

test('model presentation retains the exact action, human decision and actor context', async()=>{
  const first={...action('aabbccdd'),confirmation_request:{title:'Apply new routing',reason:'Reconnect clients to load settings'}};
  const second={...action('11223344','11'),confirmation_request:{title:'Repair connection',reason:'Restart the tunnel'}};
  const app=mount([first,second]);
  try{
    await app.settle();
    const card=app.store.getSnapshot().find(row=>row.id==='nodera-confirm:panel:aabbccdd');
    assert.equal(card.title,first.confirmation_request.title);
    assert(card.message.includes(first.confirmation_request.reason));
    assert(card.message.includes(first.service));assert(card.message.includes(first.action));
    assert.equal(app.calls.length,0);
    await card.action.run();await app.settle();
    assert.deepEqual(app.calls,[{code:first.code,decision:'confirm',telegram_id:null}]);
    assert.equal(app.queue.context.length,1);assert.equal(app.queue.context[0].actor,'panel');
    assert(app.queue.context[0].question.includes(first.operation_id));
    assert.equal(app.queue.context[0].answer,'Accepted '+first.code);
    assert.equal(app.queue.actions[0].operation_id,second.operation_id);
  }finally{app.dispose();}
});
function mount(initial, options={}) {
  const previousWindow=globalThis.window,slots=[],effects=[],timers=[],calls=[],messages=[];
  let cursor=0,queue,heldRead,hold=false;
  const store=createNotificationStore();
  const remote={actions:initial,operations:[],jobs:{}};
  const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
  const hooks={
    createContext:()=>({Provider:'provider'}),
    useState(initial){const index=cursor++;if(!(index in slots))slots[index]=initial;return [slots[index],value=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];},
    useRef(value){const index=cursor++;return slots[index]??=( {current:value} );},
    useMemo(fn,deps){const index=cursor++;if(!same(slots[index]?.deps,deps))slots[index]={deps,value:fn()};return slots[index].value;},
    useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const index=cursor++;if(!same(slots[index]?.deps,deps)){slots[index]?.cleanup?.();const entry={deps};slots[index]=entry;effects.push(()=>{entry.cleanup=fn();});}},
  };
  const notify={info:text=>messages.push(text),error:text=>messages.push(text)};
  const actual=createRequire(import.meta.url),exports={};
  const source=readFileSync(new URL('../../src/features/assistant/confirmation-queue.tsx',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}});
  const require=name=>name==='react'?hooks:name.endsWith('/notification-center')?{useNotifications:()=>store,useNotifier:()=>notify}:name.endsWith('/system-operation')?operations:name.endsWith('/api-request')?{mutationFailureState}:actual(name);
  new Function('require','exports',compiled.outputText)(require,exports);
  const request=async(path,init)=>{
    if(path.startsWith('/assistant/pending-action')){
      const response={pending_actions:structuredClone(remote.actions),operations:structuredClone(remote.operations)};
      if(hold)return new Promise(resolve=>{heldRead=()=>resolve(response);});
      return response;
    }
    if(path.startsWith('/application/operations/')){
      const id=path.split('/').at(-1);if(!remote.jobs[id])throw new Error('Not found');return structuredClone(remote.jobs[id]);
    }
    assert.equal(path,'/assistant/service-confirm');
    const body=JSON.parse(init.body);calls.push(body);
    await options.beforeDecision?.(body,calls.length);
    if(options.reject)throw Object.assign(new Error('Server busy'),{kind:'http',status:409});
    remote.actions=remote.actions.filter(row=>!(row.code===body.code&&(row.actor||'panel')===(body.telegram_id?String(body.telegram_id):'panel')));
    if(options.unknown)throw Object.assign(new Error('Lost response'),{kind:'network'});
    const result={text:'Accepted '+body.code};
    if(options.jobs){const id=body.code.repeat(4);remote.jobs[id]={id,state:options.jobState||'queued',action:'fixture'};result.operation=remote.jobs[id];}
    return result;
  };
  request.sessionKey='fixture';
  globalThis.window={setInterval(fn){timers.push(fn);return timers.length;},clearInterval(){}};
  const onOperation=()=>{};
  const render=()=>{cursor=0;queue=exports.AssistantConfirmations({request,onOperation,children:null}).props.value;while(effects.length)effects.shift()();return queue;};
  const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));render();await new Promise(resolve=>setImmediate(resolve));render();};
  render();
  return {store,remote,calls,messages,render,settle,get queue(){return queue;},
    async tick(){for(const fn of timers)fn();await settle();},
    hold(){hold=true;},release(){hold=false;heldRead?.();},
    dispose(){for(const slot of slots)slot?.cleanup?.();globalThis.window=previousWindow;},
  };
}

test('global notifications contain every actor before a chat exists and rejecting one keeps the others',async()=>{
  const app=mount([action('aabbccdd'),action('11223344'),action('eeff0011','11')]);
  try{
    await app.settle();assert.equal(app.store.getSnapshot().filter(row=>row.kind==='confirmation').length,3);
    const telegram=app.store.getSnapshot().find(row=>row.id==='nodera-confirm:11:eeff0011');
    await telegram.secondaryAction.run();await app.settle();
    assert.deepEqual(app.calls,[{code:'eeff0011',decision:'reject',telegram_id:11}]);
    assert.equal(app.queue.actions.length,2);
    assert.equal(app.store.getSnapshot().filter(row=>row.kind==='confirmation').length,2);
  }finally{app.dispose();}
});

test('double clicks submit once and a delayed poll cannot revive the accepted decision',async()=>{
  let release;const app=mount([action('aabbccdd'),action('11223344')],{beforeDecision:()=>new Promise(resolve=>{release=resolve;})});
  try{
    await app.settle();app.hold();const pendingPoll=app.tick();await new Promise(resolve=>setImmediate(resolve));
    const first=app.queue.decide(app.queue.actions[0],'confirm');
    await app.queue.decide(app.queue.actions[0],'confirm');assert.equal(app.calls.length,1);
    release();await first;app.release();await pendingPoll;await app.settle();
    assert.deepEqual(app.queue.actions.map(row=>row.code),['11223344']);
  }finally{app.dispose();}
});

test('uncertain admission is not replayed and does not dismiss another pending action',async()=>{
  const app=mount([action('aabbccdd'),action('11223344')],{unknown:true});
  try{
    await app.settle();const first=app.queue.actions[0];await app.queue.decide(first,'confirm');await app.settle();await app.tick();
    await app.queue.decide(first,'confirm');assert.equal(app.calls.length,1);
    assert.deepEqual(app.queue.actions.map(row=>row.code),['11223344']);
    assert.equal(app.store.getSnapshot().find(row=>row.id==='operation:system:'+first.operation_id).state,'unknown');
  }finally{app.dispose();}
});

test('two accepted operations reconcile independently and recover from the server after reopening',async()=>{
  const app=mount([action('aabbccdd'),action('11223344')],{jobs:true});
  try{
    await app.settle();for(const row of [...app.queue.actions])await app.queue.decide(row,'confirm');await app.settle();
    const ids=Object.keys(app.remote.jobs);
    assert.equal(ids.length,2);
    app.remote.jobs[ids[0]]={...app.remote.jobs[ids[0]],state:'succeeded',result:'success'};
    app.remote.jobs[ids[1]]={...app.remote.jobs[ids[1]],state:'running'};
    await app.tick();
    assert.equal(app.store.getSnapshot().find(row=>row.id==='operation:system:'+ids[0]).state,'success');
    assert.equal(app.store.getSnapshot().find(row=>row.id==='operation:system:'+ids[1]).state,'running');
    const restored=mount([]);
    try{
      restored.remote.operations=[{id:ids[1],action:'restart',service:'service'}];
      restored.remote.jobs[ids[1]]=app.remote.jobs[ids[1]];
      await restored.settle();await restored.tick();
      assert.equal(restored.store.getSnapshot().find(row=>row.id==='operation:system:'+ids[1]).state,'running');
    }finally{restored.dispose();}
  }finally{app.dispose();}
});

test('a rejected admission explains the error on its own card and keeps other decisions available',async()=>{
  const options={reject:true},app=mount([action('aabbccdd'),action('11223344')],options);
  try{
    await app.settle();await app.queue.decide(app.queue.actions[0],'confirm');await app.settle();
    assert.equal(app.queue.actions.length,2);
    assert.equal(app.queue.errors['panel:aabbccdd'],'Server busy');
    assert.equal(app.queue.errors['panel:11223344'],undefined);
    assert.match(app.store.getSnapshot().find(row=>row.id==='nodera-confirm:panel:aabbccdd').message,/Server busy/);
    options.reject=false;await app.queue.decide(app.queue.actions[0],'confirm');await app.settle();
    assert.deepEqual(app.queue.actions.map(row=>row.code),['11223344']);assert.equal(app.calls.length,2);
  }finally{app.dispose();}
});
