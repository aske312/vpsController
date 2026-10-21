import assert from 'node:assert/strict';
import test, {after} from 'node:test';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import {createElement, Fragment} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const loader = await createServer({configFile:false,plugins:[react()],server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true}});
after(()=>loader.close());
const {resourceIp, resourceCountry, entryCountryAddresses, countryLocationsSnapshot} = await loader.ssrLoadModule('/src/features/network/resource-country.ts');
const {CountryFlag} = await loader.ssrLoadModule('/src/shared/components/country-flag.tsx');
const {RouteResourceIcon} = await loader.ssrLoadModule('/src/features/network/route-resource-icon.tsx');

test('resource country requires agreement of all IPs and keeps uncertainty visible',()=>{
 assert.equal(resourceCountry(['8.8.8.8','1.1.1.1'],{'8.8.8.8':'US','1.1.1.1':'US'}).code,'US');
 assert.equal(resourceCountry(['8.8.8.8','1.1.1.1'],{'8.8.8.8':'US','1.1.1.1':'AU'}).code,null);
 assert.equal(resourceCountry(['8.8.8.8','1.1.1.1'],{'8.8.8.8':'US'}).code,null);
 assert.equal(resourceCountry([],{}).code,null);
 assert.equal(resourceIp('2606:4700:0:0::1111'),'2606:4700::1111');
 for(const address of ['country.example','https://8.8.8.8','999.1.1.1','008.8.8.8'])assert.equal(resourceIp(address),null);
});
test('country resolution uses resource addresses and never guesses from names',()=>{
 const host={name:'Amsterdam',client_address:'node.example',agent_url:'https://8.8.8.8:9443',addresses:{ipv4:['1.1.1.1','8.8.4.4'],ipv6:[],domains:['node.example']},excluded_addresses:['8.8.4.4']};
 assert.deepEqual(entryCountryAddresses({kind:'relay',address:'node.example',host}),['1.1.1.1']);
 assert.deepEqual(entryCountryAddresses({kind:'relay',address:'8.8.8.8',host}),['8.8.8.8']);
 assert.deepEqual(entryCountryAddresses({kind:'domain',domain:{resolved:['1.1.1.1','1.1.1.1']}}),['1.1.1.1']);
});
test('country responses are bound to requested IPs and validated',()=>{
 assert.deepEqual(countryLocationsSnapshot({items:[{address:'8.8.8.8',country_code:'US'}],pending:false},['8.8.8.8']).locations,{'8.8.8.8':'US'});
 for(const value of [{items:[],pending:false},{items:[{address:'1.1.1.1',country_code:'US'}],pending:false},{items:[{address:'8.8.8.8',country_code:'../us'}],pending:false}])assert.throws(()=>countryLocationsSnapshot(value,['8.8.8.8']));
});
test('shared inline SVG flags have unique clip paths and resource icons remain fallbacks',()=>{
 const html=renderToStaticMarkup(createElement(Fragment,null,createElement(CountryFlag,{code:'NL',label:'NL'}),createElement(CountryFlag,{code:'RU',label:'RU'})));
 const ids=[...html.matchAll(/<clipPath id="([^"]+)"/g)].map(item=>item[1]);assert.equal(ids.length,2);assert.equal(new Set(ids).size,2);
 const known=renderToStaticMarkup(createElement(RouteResourceIcon,{kind:'cdn',cdn:true,country:{code:'NL',label:'Страна IP: Нидерланды'}}));
 assert.match(known,/не скрытого сервера/);assert.doesNotMatch(known,/<img|https?:|data-kind=/);
 const unknown=renderToStaticMarkup(createElement(RouteResourceIcon,{kind:'relay',country:{code:null,label:'Страна IP не определена'}}));assert.match(unknown,/data-kind="relay"/);
 const unsupported=renderToStaticMarkup(createElement(RouteResourceIcon,{kind:'tls',country:{code:'ZZ',label:'ZZ'}}));assert.match(unsupported,/data-kind="tls"/);
});
