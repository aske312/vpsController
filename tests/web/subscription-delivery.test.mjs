import assert from 'node:assert/strict';
import test from 'node:test';
import {profileSnapshots} from '../../src/features/mihomo/workspace-refresh.ts';

const profile = {id:'profile',name:'Profile',connections:[],devices:[{id:'common',name:'Common'}]};
const at = '2026-11-24T10:11:12Z';

test('subscription delivery timestamps survive validated snapshots independently of profile modification',()=>{
 const value = {...profile,updated_at:'2026-11-20T00:00:00Z',last_subscription_fetched_at:at,
  devices:[{...profile.devices[0],last_subscription_fetched_at:at}]};
 const [saved]=profileSnapshots({items:[value]});
 assert.equal(saved.last_subscription_fetched_at,at);
 assert.equal(saved.devices[0].last_subscription_fetched_at,at);
 assert.equal(saved.updated_at,value.updated_at);
 assert.equal(profileSnapshots({items:[profile]})[0].last_subscription_fetched_at,undefined);
 for(const invalid of [null,{},0,'yesterday','2026-99-99T10:11:12Z']) {
  assert.throws(()=>profileSnapshots({items:[{...profile,last_subscription_fetched_at:invalid}]}));
  assert.throws(()=>profileSnapshots({items:[{...profile,devices:[{...profile.devices[0],last_subscription_fetched_at:invalid}]}]}));
 }
});

