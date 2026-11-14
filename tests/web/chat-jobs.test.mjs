import assert from 'node:assert/strict';
import { test, afterEach } from 'node:test';
import { createApiClient, invalidateApiSession } from '../../src/shared/lib/api-request.ts';
import { readEventStream } from '../../src/shared/lib/event-stream.ts';
import { observeChatJob, rememberChat, savedChat, ChatJobError } from '../../src/features/assistant/chat-jobs.ts';
import { observedApplicationRequest } from '../../src/control-panel/application-snapshot.ts';
import { observedServicesRequest } from '../../src/control-panel/services-snapshot.ts';
const originalFetch=globalThis.fetch;
afterEach(()=>{globalThis.fetch=originalFetch;invalidateApiSession('sse-test',false);});
const id='a'.repeat(32);
const job=(extra={})=>({id,seq:1,state:'running',stage:'waiting_model',question:'q',model:'m',stream:false,...extra});
const complete=job({state:'done',output:{result:{answer:'ok',tools_used:[]}}});
function response(parts){const encoder=new TextEncoder();return new Response(new ReadableStream({start(c){for(const part of parts)c.enqueue(typeof part==='string'?encoder.encode(part):part);c.close();}}),{headers:{'Content-Type':'text/event-stream'}});}
test('SSE assembles UTF-8 split across chunks, CRLF, multiline events, and ignores heartbeat',async()=>{
 const bytes=new TextEncoder().encode(': ping\r\ndata: {"answer":\r\ndata: "Привет"}\r\n\r\n');
 const parts=Array.from(bytes,b=>new Uint8Array([b]));const events=[];
 await readEventStream(response(parts),e=>events.push(e));assert.deepEqual(events,[{answer:'Привет'}]);
});
test('authenticated stream sends credentials only in headers and stops after logout',async()=>{
 let url,headers;globalThis.fetch=async(u,o)=>{url=u;headers=o.headers;return response(['data: {"ok":true}\n\n']);};
 const values=[];await createApiClient('sse-test').events('/assistant/requests/'+id+'/stream',v=>values.push(v),new AbortController().signal);
 assert.equal(headers.Authorization,'Basic sse-test');assert(!url.includes('sse-test'));assert.deepEqual(values,[{ok:true}]);
 const client=createApiClient('sse-test');invalidateApiSession('sse-test',false);
 await assert.rejects(client.events('/private',()=>assert.fail(),new AbortController().signal),{status:401});
});
test('terminal results are restored by ID without any POST or a second generation',async()=>{
 const methods=[];globalThis.fetch=async(u,o)=>{methods.push(o.method??'GET');return Response.json(complete);};
 const result=await observeChatJob(createApiClient('sse-test'),id,'',false,()=>{},new AbortController().signal);
 assert.equal(result.result.answer,'ok');assert.deepEqual(methods,['GET']);
});
test('control-panel observation wrappers preserve authenticated streams and restoration scope',()=>{
 const client=createApiClient('sse-test');
 const wrapped=observedServicesRequest(observedApplicationRequest(client,()=>assert.fail()));
 assert.equal(wrapped.events,client.events);assert.equal(wrapped.sessionKey,client.sessionKey);
});
test('optional streaming publishes partial snapshots then uses authoritative final result',async()=>{
 const methods=[];globalThis.fetch=async(u,o)=>{methods.push(o.method??'GET');return u.endsWith('/stream')?response([
  'data: '+JSON.stringify(job({partial:'par'}))+'\n\n', 'data: '+JSON.stringify(complete)+'\n\n']):Response.json(job());};
 const updates=[];const result=await observeChatJob(createApiClient('sse-test'),id,'',true,j=>updates.push(j),new AbortController().signal);
 assert.equal(result.result.answer,'ok');assert(updates.some(j=>j.partial==='par'));assert.deepEqual(methods,['GET','GET']);
});
test('dropped delivery reconnects by reading the same task, never replaying POST',async()=>{
 let reads=0;const calls=[];globalThis.fetch=async(u,o)=>{calls.push([u,o.method??'GET']);if(u.endsWith('/stream'))throw new TypeError('connection reset');return Response.json(++reads===1?job():complete);};
 const result=await observeChatJob(createApiClient('sse-test'),id,'',true,()=>{},new AbortController().signal);
 assert.equal(result.result.answer,'ok');assert(calls.every(([u,m])=>m==='GET'&&u.includes(id)));
});
test('provider failure stays a structured failure even after partial text',async()=>{
 globalThis.fetch=async()=>Response.json(job({state:'failed',partial:'partial',error:{code:'provider_read_timeout',message:'timed out'}}));
 await assert.rejects(observeChatJob(createApiClient('sse-test'),id,'',true,()=>{},new AbortController().signal),e=>e instanceof ChatJobError&&e.job.error.code==='provider_read_timeout');
});
test('pending references are scoped to login and actor and contain no prompts or tokens',()=>{
 const map=new Map();const previous=globalThis.sessionStorage;globalThis.sessionStorage={setItem:(k,v)=>map.set(k,v),getItem:k=>map.get(k),removeItem:k=>map.delete(k)};
 try{const first=createApiClient('sse-test');const second=createApiClient('independent-session');rememberChat(first,'11',id);
 assert.equal(savedChat(first,'11'),id);assert.equal(savedChat(first,'12'),null);assert.equal(savedChat(second,'11'),null);
 assert(!JSON.stringify([...map]).includes('sse-test'));rememberChat(first,'11',null);assert.equal(savedChat(first,'11'),null);
 }finally{globalThis.sessionStorage=previous;}
});
