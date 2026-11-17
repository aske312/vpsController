import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createApiClient} from '../../src/shared/lib/api-request.ts';
import {saveChatSession,readChatSession,savedActor,rememberActor,clearChatSessions,createChatSessionWriter} from '../../src/features/assistant/chat-session.ts';

function storage(){const entries=new Map();return {get length(){return entries.size;},key:i=>[...entries.keys()][i]??null,getItem:k=>entries.get(k)??null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k),entries};}
const snapshot={provider:'provider-one',model:'selected-model',streaming:true,conversation_id:'a'.repeat(32),prompt:'Unsent draft',turns:[{id:1,question:'Question',answer:'Answer',model:'selected-model',context_safe:true,files:[]}]};
test('typing batches history writes, reload flushes the latest draft, and logout cancels buffered data',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const previous=globalThis.sessionStorage,store=storage();globalThis.sessionStorage=store;
  const writer=createChatSessionWriter({sessionKey:'scope'},'11');
  let writes=0;const save=store.setItem;store.setItem=(...args)=>{writes++;save(...args);};
  try{
    for(let i=0;i<100;i++)writer.schedule({...snapshot,prompt:'Draft '+i});
    assert.equal(writes,0);t.mock.timers.tick(300);assert.equal(writes,1);
    assert.equal(readChatSession({sessionKey:'scope'},'11').prompt,'Draft 99');
    writer.schedule({...snapshot,prompt:'Before reload'});writer.flush();
    assert.equal(readChatSession({sessionKey:'scope'},'11').prompt,'Before reload');
    writer.schedule({...snapshot,prompt:'Private buffered draft'});
    clearChatSessions({sessionKey:'scope'});writer.dispose();t.mock.timers.tick(1000);
    assert.equal(readChatSession({sessionKey:'scope'},'11'),undefined);assert.equal(writes,2);
  }finally{writer.dispose(false);globalThis.sessionStorage=previous;}
});

test('switching actors flushes the previous draft into its own scope and keeps request trace IDs',()=>{
  const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
  const client={sessionKey:'scope'},first=createChatSessionWriter(client,'11');
  const second=createChatSessionWriter(client,'22');
  try{
    first.schedule({...snapshot,prompt:'First actor',turns:[{...snapshot.turns[0],request_id:'b'.repeat(32)}]});first.dispose();
    second.schedule({...snapshot,prompt:'Second actor'});second.flush();
    assert.equal(readChatSession(client,'11').prompt,'First actor');
    assert.equal(readChatSession(client,'11').turns[0].request_id,'b'.repeat(32));
    assert.equal(readChatSession(client,'22').prompt,'Second actor');
  }finally{first.dispose(false);second.dispose(false);globalThis.sessionStorage=previous;}
});
test('a refreshed client restores its actor and chat without crossing account or actor boundaries',()=>{
  const previous=globalThis.sessionStorage,store=storage();globalThis.sessionStorage=store;
  try{
    const client=createApiClient('auth-secret'),other=createApiClient('different-account');
    rememberActor(client,'11');saveChatSession(client,'11',snapshot);
    const refreshed=createApiClient('auth-secret');assert.equal(savedActor(refreshed),'11');assert.deepEqual(readChatSession(refreshed,'11'),{...snapshot,turns:[{...snapshot.turns[0],tools:[],error:undefined}]});
    assert.equal(readChatSession(other,'11'),undefined);assert.equal(readChatSession(refreshed,'12'),undefined);
    assert(!JSON.stringify([...store.entries]).includes('auth-secret'));
    saveChatSession(other,'11',snapshot);clearChatSessions(client);
    assert.equal(readChatSession(refreshed,'11'),undefined);assert(readChatSession(other,'11'));
  }finally{globalThis.sessionStorage=previous;}
});
test('reload snapshots exclude file bodies and in-flight jobs, and never replay unsent queued messages',()=>{
  const previous=globalThis.sessionStorage,store=storage();globalThis.sessionStorage=store;
  try{
    const client={sessionKey:'test-scope'};
    saveChatSession(client,'',{...snapshot,turns:[{...snapshot.turns[0],files:[{data:'private-file-body'}]},{id:2,question:'running',model:'m',queue_state:'running'},{id:3,question:'queued',model:'m',queue_state:'queued'}]});
    const restored=readChatSession(client,'');assert.equal(restored.turns.length,2);assert.equal(restored.turns[1].question,'queued');assert.match(restored.turns[1].error,/Не отправлено/);
    assert(!JSON.stringify([...store.entries]).includes('private-file-body'));assert(!JSON.stringify(restored).includes('queue_state'));
    const identity=[...store.entries.keys()].find(k=>k.endsWith(':panel'));store.setItem(identity,'{broken');assert.equal(readChatSession(client,''),undefined);
  }finally{globalThis.sessionStorage=previous;}
});
