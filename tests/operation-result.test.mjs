import assert from 'node:assert/strict';
import test from 'node:test';
import { operationCompleted, OperationFailedError } from '../src/operation-result.ts';

test('running services with Result=success are not completed operations',()=>{
  for(const state of ['active','activating','running'])assert.equal(operationCompleted({unit:'expected',state,result:'success'},'expected'),false);
  assert.equal(operationCompleted({unit:'expected',state:'succeeded',result:'success'},'expected'),true);
});
test('another operation and a missing result cannot complete the requested update',()=>{
  assert.equal(operationCompleted({unit:'other',state:'succeeded',result:'success'},'expected'),false);
  assert.equal(operationCompleted({unit:'expected',state:'succeeded',result:'unknown'},'expected'),false);
});
test('a failure immediately propagates its server reason regardless of message wording',()=>{
  for(const [state,result] of [['failed','failed'],['active','exit-code']])assert.throws(()=>operationCompleted({unit:'expected',state,result,message:'Сбой установки пакета'},'expected'),error=>error instanceof OperationFailedError&&error.message==='Сбой установки пакета');
  assert.equal(operationCompleted({unit:'other',state:'failed',result:'exit-code'},'expected'),false);
});
