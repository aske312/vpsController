import assert from "node:assert/strict";
import test from "node:test";
import { applicationStatusSnapshot, applicationStatusUnavailable, observedApplicationRequest } from "../../src/control-panel/application-snapshot.ts";
import { systemActionSnapshot, systemActionNeedsPolling } from "../../src/control-panel/system-operation.ts";
import { createApiClient } from "../../src/shared/lib/api-request.ts";
import { cdnSecuritySnapshot } from "../../src/shared/lib/cdn-security-operation.ts";

const snapshot=value=>applicationStatusSnapshot(value,systemActionSnapshot,cdnSecuritySnapshot);
const status={api:{active:true,enabled:true},containers:[{Service:"web",State:"running",healthy:true}],
  action:{id:"worker",unit:"worker.service",action:"mihomo-profile-update",state:"running",progress:50}};

test("valid legacy and observed application states retain their operation",()=>{
  assert.deepEqual(snapshot(status),status);
  assert.deepEqual(snapshot({...status,action:{},api:{active:null,enabled:null}}).action,{});
});

test("malformed application observations cannot reach component and operation renderers",()=>{
  for(const value of [null,{}, {...status,api:{active:"true",enabled:true}}, {...status,containers:{}},
    {...status,containers:[{Service:{}}]}, {...status,containers:[{runtime:{state:"running",reason:{},checked_at:"now"}}]},
    {...status,action:{...status.action,message:{}}}, {...status,runtime:{mode:"systemd",migration_required:"false"}},
    {...status,recovery:{available:"true"}}, {...status,recovery:{application_required:"false"}}, {...status,recovery:{application_command:"update"}}, {...status,service_mode:{active:[]}},
    {...status,cdn_security:{authenticated_origin_pulls:"false"}}]) assert.throws(()=>snapshot(value));
});

test("failed application read makes live observations unknown while preserving the pending identity",()=>{
  const value=applicationStatusUnavailable(status,systemActionNeedsPolling(status.action));
  assert.equal(value.api.active,null);assert.equal(value.api.enabled,null);
  assert.equal(value.containers[0].healthy,null);assert.equal(value.containers[0].State,"unknown");
  assert.equal(value.containers[0].Service,"web");assert.equal(value.action.id,status.action.id);
  assert.equal(value.action.unit,status.action.unit);assert.equal(value.action.state,"unknown");
  assert.equal(value.action.progress,undefined);assert.equal(systemActionNeedsPolling(value.action),true);
  const completed={...status,action:{...status.action,state:"succeeded",result:"success"}};
  assert.equal(applicationStatusUnavailable(completed,false).action,completed.action);
  assert.equal(applicationStatusUnavailable(null,false),null);
});

test("application polling bypasses cache and rejects bad operations before any state publication",async()=>{
  const previous=globalThis.fetch;let calls=0,mode="valid";
  try{
    globalThis.fetch=async()=>{calls++;return Response.json(mode==="valid"?status:{...status,action:{...status.action,message:{}}});};
    const request=observedApplicationRequest(createApiClient("observed-application",{retryDelaysMs:[]}),snapshot);
    await request("/application/status");mode="bad";
    await assert.rejects(request("/application/status"));assert.equal(calls,2);
    mode="valid";assert.deepEqual(await request("/application/status"),status);assert.equal(calls,3);
    assert.deepEqual(await request("/other-endpoint"),status);
  }finally{globalThis.fetch=previous;}
});
