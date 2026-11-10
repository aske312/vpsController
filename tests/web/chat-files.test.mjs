import assert from 'node:assert/strict';
import test from 'node:test';
import { File } from 'node:buffer';
import { chatHistory, readChatFiles } from '../../src/features/assistant/chat-files.ts';

test('attachments encode content, preserve names and reject unsupported or oversized files', async () => {
  const [file] = await readChatFiles([new File(['hello'], 'notes.txt')], []);
  assert.equal(file.name, 'notes.txt');
  assert.equal(file.mime, 'text/plain');
  assert.equal(atob(file.data), 'hello');
  await assert.rejects(readChatFiles([new File(['x'], 'program.exe')], []));
  await assert.rejects(readChatFiles([new File(['x'.repeat(2 * 1024 * 1024 + 1)], 'large.txt')], []));
  await assert.rejects(readChatFiles([new File(['x'], 'a.txt')], [file, file, file, file]));
});

test('conversation history carries prior documents and respects shared byte and text budgets', () => {
  const file = { id: 'one', name: 'notes.txt', mime: 'text/plain', data: 'aGVsbG8=', size: 5 };
  const history = chatHistory([{ question: '', answer: 'Summary', files: [file] }, { question: 'Explain', answer: 'Explanation' }]);
  assert.equal(history.length, 4);
  assert.equal(history[0].attachments[0].data, file.data);
  assert.deepEqual(history.map(row => row.role), ['user', 'assistant', 'user', 'assistant']);
  assert.equal(chatHistory(Array.from({ length: 4 }, () => ({ question: 'x'.repeat(8000), answer: 'y'.repeat(8000) }))).length, 6);
  assert.equal(chatHistory([{ question: 'Previous', answer: 'Answer', files: [{ ...file, size: 4 * 1024 * 1024 }] }], [{ ...file, size: 3 * 1024 * 1024 }]).length, 0);
});
