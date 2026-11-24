import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createApiClient} from '../../src/shared/lib/api-request.ts';
import {saveChatSession,readChatSession,savedActor,rememberActor,clearChatSessions,clearClientChatSessions,createChatSessionWriter} from '../../src/features/assistant/chat-session.ts';
import {commandMessageOwner,closedCommandMessages,rememberClosedCommand} from '../../src/features/assistant/command-message-state.ts';

function storage(){const entries=new Map();return {get length(){return entries.size;},key:i=>[...entries.keys()][i]??null,getItem:k=>entries.get(k)??null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k),entries};}
const snapshot={provider:'provider-one',model:'selected-model',streaming:true,conversation_id:'a'.repeat(32),prompt:'Unsent draft',turns:[{id:1,question:'Question',answer:'Answer',model:'selected-model',context_safe:true,files:[]}]};

test('panel privacy migration removes old client transcripts and preserves its own chat',()=>{
 const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
 try {
  const client={sessionKey:'privacy'};
  saveChatSession(client,'11',snapshot);saveChatSession(client,'22',snapshot);saveChatSession(client,'',snapshot);
  saveChatSession({sessionKey:'other'},'11',snapshot);
  clearClientChatSessions(client);
  assert.equal(readChatSession(client,'11'),undefined);assert.equal(readChatSession(client,'22'),undefined);
  assert.equal(readChatSession(client,'').prompt,snapshot.prompt);
  assert.equal(readChatSession({sessionKey:'other'},'11').prompt,snapshot.prompt);
 } finally {globalThis.sessionStorage=previous;}
});

test('finished response duration survives a local chat reload',()=>{
 const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
 try {
  saveChatSession({sessionKey:'timing'},'11',{...snapshot,turns:[{...snapshot.turns[0],duration_ms:42123}]});
  assert.equal(readChatSession({sessionKey:'timing'},'11').turns[0].duration_ms,42123);
 } finally {globalThis.sessionStorage=previous;}
});

test('operation links survive reload without confirmation codes and closed results are scoped and cleared on logout',()=>{
  const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
  try {
    const scope={sessionKey:'commands'},id='b'.repeat(32),second='c'.repeat(32);
    saveChatSession(scope,'11',{...snapshot,turns:[{...snapshot.turns[0],pending_actions:[{operation_id:id,code:'secret-code'}]},{...snapshot.turns[0],id:2,pending_action:{operation_id:second}}]});
    const turns=readChatSession(scope,'11').turns;
    assert.equal(commandMessageOwner(turns,[id]),1);assert.equal(commandMessageOwner(turns,[second]),2);
    assert.equal(commandMessageOwner([...turns,{id:3,command_ids:[id]}],[id]),1);
    assert.equal(commandMessageOwner(turns,['d'.repeat(32)]),undefined);
    assert(!JSON.stringify([...sessionStorage.entries.values()]).includes('secret-code'));
    rememberClosedCommand(scope,id);assert(closedCommandMessages(scope).has(id));
    assert.equal(closedCommandMessages({sessionKey:'another'}).size,0);
    clearChatSessions(scope);assert.equal(closedCommandMessages(scope).size,0);
  } finally {globalThis.sessionStorage=previous;}
});

test('hidden decisions retain model context across reload without becoming visible dialogue turns',()=>{
  const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
  try{
    const decision={id:2,question:'Confirmed routing change',answer:'Operation queued',model:'',context_safe:true,system_event:true,files:[]};
    saveChatSession({sessionKey:'scope'},'11',{...snapshot,turns:[...snapshot.turns,decision]});
    const restored=readChatSession({sessionKey:'scope'},'11');
    assert.equal(restored.turns[1].system_event,true);
    assert.equal(restored.turns[1].context_safe,true);
    assert.equal(restored.turns.filter(turn=>!turn.system_event).length,1);
  }finally{globalThis.sessionStorage=previous;}
});
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
    first.schedule({...snapshot,prompt:'First actor',turns:[{...snapshot.turns[0],request_id:'b'.repeat(32),status:'partial'}]});first.dispose();
    second.schedule({...snapshot,prompt:'Second actor'});second.flush();
    assert.equal(readChatSession(client,'11').prompt,'First actor');
    assert.equal(readChatSession(client,'11').turns[0].request_id,'b'.repeat(32));
    assert.equal(readChatSession(client,'11').turns[0].status,'partial');
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
test('reload snapshots exclude file bodies and server-owned queued jobs rather than declaring them unsent',()=>{
  const previous=globalThis.sessionStorage,store=storage();globalThis.sessionStorage=store;
  try{
    const client={sessionKey:'test-scope'};
    saveChatSession(client,'',{...snapshot,turns:[{...snapshot.turns[0],files:[{data:'private-file-body'}]},{id:2,question:'running',model:'m',queue_state:'running'},{id:3,question:'queued',model:'m',queue_state:'queued'}]});
    const restored=readChatSession(client,'');assert.equal(restored.turns.length,1);assert.doesNotMatch(JSON.stringify(restored),/Не отправлено/);
    assert(!JSON.stringify([...store.entries]).includes('private-file-body'));assert(!JSON.stringify(restored).includes('queue_state'));
    const identity=[...store.entries.keys()].find(k=>k.endsWith(':panel'));store.setItem(identity,'{broken');assert.equal(readChatSession(client,''),undefined);
  }finally{globalThis.sessionStorage=previous;}
});
