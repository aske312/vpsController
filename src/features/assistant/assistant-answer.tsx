import { Fragment, useState, type ReactNode } from 'react';
import { answerBlocks, tableRows } from './answer-format';
import { ChatIcon } from './chat-icon';

function inline(text: string): ReactNode[] {
    return text.split(/(`[^`\n]+`|\[[^\]\n]{1,200}\]\(https?:\/\/[^\s<>]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*|~~[^~\n]+~~)/g).map((part, index) => {
      const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s<>]+)\)$/);
      if (link) {
        try {
          const url = new URL(link[2]);
          if (!url.username && !url.password) return <a key={index} href={url.href} target="_blank" rel="noopener noreferrer nofollow">{link[1]}</a>;
        } catch { /* Malformed source links remain literal text. */ }
      }
      return part.startsWith('`') && part.endsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith('*') && part.endsWith('*') ? <em key={index}>{part.slice(1, -1)}</em> : part.startsWith('~~') && part.endsWith('~~') ? <del key={index}>{part.slice(2, -2)}</del> : part;
    });
}
function prose(text: string) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const content: ReactNode[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] ?? '')) {
      const group = [line]; index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) group.push(lines[index++]);
      const [header, ...rows] = tableRows(group);
      content.push(<div className="mcpAnswerTable" key={index}><table><thead><tr>{header.map((cell, col) => <th key={col}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, num) => <tr key={num}>{header.map((_, col) => <td key={col}>{inline(row[col] ?? '')}</td>)}</tr>)}</tbody></table></div>); continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+)/);
    if (heading) { content.push(<h3 key={index}>{inline(heading[1])}</h3>); index++; continue; }
    const list = line.match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)/);
    if (list) {
      const ordered = !list[1], items = []; const first = index;
      while (index < lines.length) {
        const match = lines[index].match(ordered ? /^\s*\d+[.)]\s+(.+)/ : /^\s*[-*+]\s+(.+)/);
        if (!match) break;
        items.push(<li key={index}>{inline(match[1])}</li>); index++;
      }
      content.push(ordered ? <ol key={first}>{items}</ol> : <ul key={first}>{items}</ul>); continue;
    }
    const paragraph = [line]; index++;
    while (index < lines.length && lines[index].trim() && !/^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)/.test(lines[index]) && !(lines[index].includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] ?? ''))) paragraph.push(lines[index++]);
    content.push(<p key={index}>{paragraph.map((part, num) => <Fragment key={num}>{num > 0 && <br/>}{inline(part)}</Fragment>)}</p>);
  }
  return content;
}
export function AssistantAnswer({ text }: { text: string }) {
  const [copied, setCopied] = useState<number>();
  return <div className="mcpAnswerContent">{answerBlocks(text).map((block, index) => block.type === 'code' ? <div className="mcpCodeBlock" key={index}><header><span>{block.language || 'Код'}</span><button type="button" className="mcpIconButton" aria-label="Скопировать код" title={copied === index ? 'Скопировано' : 'Скопировать код'} onClick={() => { void navigator.clipboard.writeText(block.text).then(() => setCopied(index)).catch(() => setCopied(undefined)); }}><ChatIcon name={copied === index ? 'check' : 'copy'}/></button></header><pre><code>{block.text}</code></pre></div> : <Fragment key={index}>{prose(block.text)}</Fragment>)}</div>;
}
