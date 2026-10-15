import assert from "node:assert/strict";
import test from "node:test";
import { moduleSnapshots, profileSnapshots } from "../../src/features/mihomo/workspace-refresh.ts";

const moduleFixture = {id:"transport-wg",name:"WG",category:"transport",installed:true,settings_values:{mtu:1420},settings:[{key:"mtu"}]};
const profile = {id:"one",name:"Home",connections:[{id:"wg",component:"transport-wg",settings:{}}],devices:[{id:"common",name:"Common",routing:{}}]};

test("valid and explicitly empty collections keep their meaning", () => {
  assert.deepEqual(moduleSnapshots({items:[]}),[]);
  assert.deepEqual(profileSnapshots({items:[]}),[]);
  assert.equal(moduleSnapshots({items:[moduleFixture]})[0],moduleFixture);
  assert.equal(profileSnapshots({items:[profile]})[0],profile);
  assert.equal(profileSnapshots({items:[{...profile,name:""}]})[0].name,"");
});

test("missing, malformed and ambiguous lists cannot become empty lists", () => {
  for(const read of [moduleSnapshots,profileSnapshots]) {
    for(const value of [null,{},[],{items:null},{items:{}},{items:[null]},{items:[[]]},{items:[{id:"one"}]},
      {items:[profile,profile]}]) assert.throws(()=>read(value),Error);
  }
});

test("broken module state and settings cannot replace the previous snapshot", () => {
  for(const change of [{installed:"false"},{category:"unknown"},{settings_values:[]},{settings:"bad"},
    {settings:[null]},{connection_settings:["field"]}]) assert.throws(()=>moduleSnapshots({items:[{...moduleFixture,...change}]}),Error);
});

test("broken nested profile records cannot crash the editor or look deleted", () => {
  for(const change of [{connections:null},{connections:{}},{connections:[null]},{connections:[{id:"wg"}]},
    {connections:[{id:"wg",component:"transport-wg",settings:[]}]},{routing:[]},{devices:[null]},
    {devices:[{id:"common",routing:"bad"}]}]) assert.throws(()=>profileSnapshots({items:[{...profile,...change}]}),Error);
});

test("rendered module fields and setting choices reject objects and duplicate identities", () => {
  for (const change of [{description:{}},{active:"true"},{settings_values:{mtu:[]}},
    {settings:[{key:"mtu",label:{}}]},{settings:[{key:"mtu",options:[{}]}]},
    {connection_settings:[{key:"sni",formats:{}}]},{settings:[{key:"mtu"},{key:"mtu"}]}]) {
    assert.throws(()=>moduleSnapshots({items:[{...moduleFixture,...change}]}));
  }
});

test("nested profile identities, route snapshots and device capabilities must be usable", () => {
  const connection=profile.connections[0],device=profile.devices[0];
  for (const change of [{channels:{}},{connections:[connection,connection]},
    {connections:[{...connection,name:{}}]},{connections:[{...connection,settings:{port:[]}}]},
    {connections:[{...connection,route_state:{state:"stale",reason:"removed",addresses:null,bindings:[]}}]},
    {devices:[device,device]},{devices:[{...device,name:{}}]},{devices:[{...device,os:{}}]},
    {devices:[{...device,supported_formats:"mihomo"}]},{devices:[{...device,personal_rule_ids:{}}]},
    {devices:[{...device,verified_clients:[{}]}]},{routing:{strategy:[]}}]) {
    assert.throws(()=>profileSnapshots({items:[{...profile,...change}]}));
  }
});

test("profile protection observations preserve real counts and transition evidence", () => {
  const protection = { vless_connections: 1, vless_connections_by_route: { reality: 1, cdn: 1, tls: 0 }, encryption_pending: false, previous_connections: 1, previous_valid_until: 1800000000.5, yaml_served_at: 1700000000.25 };
  const observed = { ...profile, subscription_status: "active", protection_status: { common: protection } };
  assert.equal(profileSnapshots({ items: [observed] })[0], observed);
  assert.equal(profileSnapshots({ items: [{ ...profile, protection_status: {} }] }).length, 1);
  assert.equal(profileSnapshots({ items: [{ ...profile, protection_status: { common: { vless_connections: 0, encryption_pending: false } } }] }).length, 1);
});

test("malformed protection, transition or receipt fields cannot replace the profile baseline", () => {
  const protection = { vless_connections: 1, encryption_pending: false };
  for (const change of [{ protection_status: [] }, { protection_status: { common: null } },
    ...[{ vless_connections: "1" }, { vless_connections: -1 }, { vless_connections: 1.5 }, { encryption_pending: "false" },
      { vless_connections_by_route: {} }, { vless_connections_by_route: { reality: 2, cdn: 0, tls: 0 } },
      { previous_connections: {} }, { previous_valid_until: {} }, { previous_valid_until: -1 }, { previous_valid_until: 8.64e12 + 1 }, { yaml_served_at: Infinity }]
      .map(change => ({ protection_status: { common: { ...protection, ...change } } })),
    { subscription_status: "unknown" }, { revision: {} }, { create_revision: [] }, { last_operation_id: {} }]) {
    assert.throws(() => profileSnapshots({ items: [{ ...profile, ...change }] }));
  }
});
