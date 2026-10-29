import assert from 'node:assert/strict';
import test, {after} from 'node:test';
import { createTestLoader } from "./vite-test-loader.mjs";
import react from '@vitejs/plugin-react';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const loader = await createTestLoader(import.meta.url, [react()]);
after(()=>loader.close());
const {routePage,RoutePagination}=await loader.ssrLoadModule('/src/features/network/route-pagination.tsx');

test('address pagination is hidden through 15 and limits every page to 15',()=>{
 for(const count of [0,1,15])assert.equal(renderToStaticMarkup(createElement(RoutePagination,{...routePage(Array.from({length:count}),1),label:'IP',onChange:()=>{}})),'');
 const items=Array.from({length:31},(_,index)=>index);
 assert.deepEqual(routePage(items,1).items,items.slice(0,15));
 assert.deepEqual(routePage(items,2).items,items.slice(15,30));
 assert.deepEqual(routePage(items,3).items,[30]);
 assert.deepEqual([routePage(items,3).from,routePage(items,3).to],[31,31]);
 assert.match(renderToStaticMarkup(createElement(RoutePagination,{...routePage(items.slice(0,16),1),label:'IP',onChange:()=>{}})),/1–15 из 16/);
});
test('pagination clamps deleted or filtered pages and keeps independent range state',()=>{
 const entries=routePage(Array.from({length:47}),3), ips=routePage(Array.from({length:20}),1);
 assert.equal(entries.page,3);assert.equal(ips.page,1);
 assert.equal(routePage(Array.from({length:16}),entries.page).page,2);
 assert.equal(routePage([],entries.page).page,1);
 assert.equal(routePage([1],0).page,1);
});
