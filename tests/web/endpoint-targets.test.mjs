import assert from 'node:assert/strict';
import test,{after} from 'node:test';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import {routeCheck} from './network-fixtures.mjs';
const loader=await createServer({configFile:false,plugins:[react()],server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true}});after(()=>loader.close());
const {endpointTargets}=await loader.ssrLoadModule('/src/features/network/use-endpoint-sweep.ts');
const target={id:'direct:tuic:a',owner:'direct',component:'tuic',label:'TUIC',consumer_label:'Phone',consumer_id:'direct:a',state:'pending',message:''};
const snapshot={kind:'udp_relay',domain:'relay.example',items:[target],observation:routeCheck};
test('all-component inventory remains bound to address and explicit consumer identities',()=>{
 assert.equal(endpointTargets(snapshot,'udp_relay','relay.example').items[0].consumer_id,'direct:a');
 for(const value of [{...snapshot,domain:'other.example'},{...snapshot,kind:'cdn'},{...snapshot,items:[target,target]},{...snapshot,items:[{...target,consumer_id:null}]},{...snapshot,items:[{...target,state:'ready'}]}])assert.throws(()=>endpointTargets(value,'udp_relay','relay.example'));
});
