import assert from 'node:assert/strict';
import test from 'node:test';
import {commandMatches,directCommand} from '../../src/features/assistant/chat-commands.ts';

test('an exact slash command takes priority over longer commands with the same prefix',()=>{
  const items=['/models','/model','/services','/service','/profiles','/profile'].map(command=>({command}));
  for(const command of ['/model','/service','/profile'])assert.equal(commandMatches(items,command)[0].command,command);
  assert.deepEqual(commandMatches(items,'/serv').map(row=>row.command),['/services','/service']);
  assert.deepEqual(items.map(row=>row.command),['/models','/model','/services','/service','/profiles','/profile']);
});

test('only the explicit ai prefix enters generation; other slash commands stay direct',()=>{
  for(const value of ['/status','/service mihomo restart',' /unknown','/air'])assert.equal(directCommand(value),true);
  for(const value of ['Explain this','/ai Explain this','/AI','/ai /etc/config'])assert.equal(directCommand(value),false);
});
