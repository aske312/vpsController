import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import react from '@vitejs/plugin-react';
import { createTestLoader } from './vite-test-loader.mjs';
import { answerBlocks, conversationMarkdown } from '../../src/features/assistant/answer-format.ts';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { AssistantAnswer } = await loader.ssrLoadModule('/src/features/assistant/assistant-answer.tsx');

test('multiline code and symbols stay literal, including unfinished fences and Windows newlines', () => {
  const blocks = answerBlocks('Before\r\n```py\r\nx = "<>&_*"\r\ny = 2\r\n```\r\nAfter');
  assert.equal(blocks[1].type, 'code');
  assert.equal(blocks[1].text, 'x = "<>&_*"\ny = 2');
  assert.equal(blocks[2].text.trim(), 'After');
  assert.equal(answerBlocks('```txt\na\nb')[0].text, 'a\nb');
});

test('responses render semantic Markdown while raw HTML and scripts remain text', () => {
  const html = renderToStaticMarkup(createElement(AssistantAnswer, { text: '# Report\n\n**Ready** and *clear* `a_b`\n\n- One\n- Two\n\n| Name | Value |\n| --- | --- |\n| CPU | 12% |\n\n<script>alert(1)</script>\n\n```js\nx < y && y > z\n```' }));
  for (const tag of ['<h3>', '<strong>', '<em>', '<ul>', '<table>', '<pre>']) assert.ok(html.includes(tag));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('x &lt; y &amp;&amp; y &gt; z'));
});

test('export contains dialogue text and file names without embedding attachment bytes', () => {
  const markdown = conversationMarkdown([{ question: 'Read', answer: 'Summary', model: 'test', files: [{ name: 'a.txt', data: 'private-base64' }] }]);
  assert.ok(markdown.includes('a.txt'));
  assert.ok(markdown.includes('Summary'));
  assert.ok(!markdown.includes('private-base64'));
});
