import assert from "node:assert/strict";
import test from "node:test";
import { personalRuleSnapshot, policySnapshot, presetsSnapshot } from "../../src/features/mihomo/workspace-refresh.ts";

const dns={schema:[{key:"mode",label:"Mode",type:"select",options:["fake-ip",{value:"redir-host",label:"Redir"}]}],values:{mode:"fake-ip"}};
const preset={id:"preset",name:"Preset",description:"",strategy:"select",components:[{id:"transport-wg"}]};
const rule={id:"personal-one",code:"ONE",title:"One",description:"",kind:"domain",target:"DIRECT",entries:["example.com"]};
const routing={schema:[],values:{mode:"rule"},presets:[preset],personal_rules:[rule],rule_lists:[{id:"domains",key:"domains",title:"Domains",default_rules:"example.com",using_default:true}]};

test("valid policy schemas and nested records keep their values",()=>{
  assert.equal(policySnapshot(dns,"dns"),dns);
  assert.equal(policySnapshot(routing,"routing"),routing);
  assert.equal(personalRuleSnapshot(rule),rule);
  assert.deepEqual(presetsSnapshot({presets:[preset]}),{presets:[preset]});
});

test("broken schemas or values cannot replace a draft baseline",()=>{
  for(const value of [null,{}, {...dns,schema:null},{...dns,values:[]},{...dns,values:{mode:null}},
    {...dns,values:{count:Infinity}},{...dns,revision:"invalid"},{...dns,schema:["field"]},{...dns,schema:[dns.schema[0],dns.schema[0]]},
    {...dns,schema:[{...dns.schema[0],help:{}}]},{...dns,schema:[{...dns.schema[0],default:[]}]},
    {...dns,schema:[{...dns.schema[0],options:[{}]}]},{...dns,schema:[{...dns.schema[0],type:"unknown"}]}]){
    assert.throws(()=>policySnapshot(value,"dns"),Error);
  }
});

test("missing routing collections are unavailable instead of empty",()=>{
  for(const key of ["presets","personal_rules","rule_lists"]){
    const missing={...routing};delete missing[key];
    assert.throws(()=>policySnapshot(missing,"routing"),Error);
    assert.throws(()=>policySnapshot({...routing,[key]:null},"routing"),Error);
  }
});

test("malformed presets and personal rules never reach editor callbacks",()=>{
  for(const change of [{components:null},{components:[null]},{strategy:"unknown"},{description:{}},{components:[{id:"transport-wg",label:{}}]}])assert.throws(()=>presetsSnapshot({presets:[{...preset,...change}]}),Error);
  assert.throws(()=>presetsSnapshot({presets:[preset,preset]}),Error);
  for(const change of [{entries:null},{entries:[{}]},{kind:"unknown"},{target:"unknown"},{description:{}}])assert.throws(()=>personalRuleSnapshot({...rule,...change}),Error);
  assert.throws(()=>policySnapshot({...routing,personal_rules:[rule,rule]},"routing"),Error);
  assert.throws(()=>policySnapshot({...routing,rule_lists:[{...routing.rule_lists[0],default_rules:[]}]},"routing"),Error);
});
