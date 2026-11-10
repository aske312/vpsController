export type AnswerBlock = { type: 'text'; text: string } | { type: 'code'; text: string; language: string };
export function answerBlocks(text: string): AnswerBlock[] {
  text = text.replace(/\r\n?/g, '\n');
  const blocks: AnswerBlock[] = [];
  const fence = /^```([^\n`]*)\n([\s\S]*?)(?:\n```[ \t]*(?=\n|$)|(?![\s\S]))/gm;
  let end = 0;
  for (const match of text.matchAll(fence)) {
    if (match.index! > end) blocks.push({ type: 'text', text: text.slice(end, match.index) });
    blocks.push({ type: 'code', language: match[1].trim().slice(0, 30), text: match[2] });
    end = match.index! + match[0].length;
  }
  if (end < text.length) blocks.push({ type: 'text', text: text.slice(end) });
  return blocks;
}
export function tableRows(lines: string[]) {
  return lines.map(line => line.replace(/^\s*\||\|\s*$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|')));
}
export function conversationMarkdown(turns: { question: string; model: string; answer?: string; error?: string; files?: { name: string }[] }[]) {
  return '# Диалог с Нодерой\n\n' + turns.map(turn => '## Вы\n\n' + turn.question +
    (turn.files?.length ? '\n\nВложения: ' + turn.files.map(file => file.name).join(', ') : '') +
    '\n\n## Нодера · ' + turn.model + '\n\n' + (turn.answer || turn.error || 'Ответ ожидается')).join('\n\n---\n\n') + '\n';
}
