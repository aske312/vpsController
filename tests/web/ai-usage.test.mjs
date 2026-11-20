import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import react from '@vitejs/plugin-react';
import { createTestLoader } from './vite-test-loader.mjs';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { usageByUser, checkedUsageReport, AIUsageTable } = await loader.ssrLoadModule('/src/features/assistant/ai-usage.tsx');
const row = {actor:'11',channel:'bot',provider:'a'.repeat(32),model:'m1',requests:1,completed:1,incomplete:0,
 failed:0,unknown:0,model_calls:3,reported_calls:2,prompt_tokens:100,completion_tokens:20,retries:1,unmeasured_requests:1};

test('usage sums models and channels for one user without mixing another user or admin',()=>{
 const users=usageByUser([row,{...row,model:'m2',channel:'panel'},{...row,actor:'22'},{...row,actor:'panel',channel:'panel'}]);
 assert.equal(users.length,3);
 const first=users.find(u=>u.actor==='11');
 assert.equal(first.requests,2);assert.equal(first.model_calls,6);assert.equal(first.prompt_tokens,200);
 assert.deepEqual(first.models,['m1','m2']);assert.equal(row.requests,1);
});

test('partial accounting is labelled and unknown usage is never presented as zero spend',()=>{
 const html=renderToStaticMarkup(createElement(AIUsageTable,{users:usageByUser([row,{...row,actor:'22',reported_calls:0,prompt_tokens:0,completion_tokens:0}]),names:{'11':'<script>Owner</script>'}}));
 assert(html.includes('Учёт неполный'));assert(html.includes('120'));
 assert(html.includes('—'));assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
 assert(html.includes('Повторов: 1'));
});

test('malformed usage is rejected before it can replace the last valid report',()=>{
 const valid={days:30,rows:[row],earliest_record_at:null,retention_days:90,cost_available:false};
 assert.equal(checkedUsageReport(valid),valid);
 for(const value of [null,{}, {...valid,rows:[{...row,model_calls:-1}]},{...valid,rows:[{...row,prompt_tokens:'100'}]}]) {
  assert.throws(()=>checkedUsageReport(value));
 }
});
