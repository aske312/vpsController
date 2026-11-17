export type ChatFile = { id: string; name: string; mime: string; data: string; size: number };
export const fileAccept = '.png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.csv,.json,.log,.yaml,.yml,.xml';
export const maxFileBytes = 2 * 1024 * 1024;
const types: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp',
  pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  txt: 'text/plain', md: 'text/plain', csv: 'text/plain', json: 'text/plain', log: 'text/plain', yaml: 'text/plain', yml: 'text/plain', xml: 'text/plain' };
export async function readChatFiles(files: File[], current: ChatFile[]): Promise<ChatFile[]> {
  if (files.length + current.length > 4) throw new Error('До 4 файлов в сообщении');
  if (files.reduce((sum, file) => sum + file.size, current.reduce((sum, file) => sum + file.size, 0)) > 6 * 1024 * 1024) throw new Error('Общий размер файлов — до 6 МБ');
  return Promise.all(files.map(async file => {
    const mime = types[file.name.split('.').pop()?.toLowerCase() ?? ''];
    if (!mime) throw new Error('Поддерживаются изображения, PDF, DOCX и текстовые файлы');
    if (!file.size || file.size > maxFileBytes) throw new Error('Размер каждого файла — до 2 МБ');
    if (file.name.length > 160 || /[\x00-\x1f/\\]/.test(file.name)) throw new Error('Недопустимое имя файла');
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    return { id: crypto.randomUUID(), name: file.name, mime, data: btoa(binary), size: file.size };
  }));
}
export function chatHistory(turns: { question: string; answer?: string; error?: string; status?: string; queue_state?: string; request_id?: string; files?: ChatFile[]; context_safe?: boolean }[], currentFiles: ChatFile[] = []) {
  const result: { role: 'user' | 'assistant'; content: string; attachments?: { name: string; mime: string; data: string }[] }[] = [];
  let length = 0;
  let bytes = currentFiles.reduce((sum, file) => sum + file.size, 0);
  for (const turn of turns.filter(turn => turn.answer && !turn.error && turn.queue_state !== 'running' && turn.context_safe !== false).slice(-20).reverse()) {
    let question = turn.question.slice(0, 8000) || 'Файлы', answer = turn.answer!.slice(0, 8000);
    let files = turn.files;
    let fileBytes = files?.reduce((sum, file) => sum + file.size, 0) ?? 0;
    if (bytes + fileBytes > 6 * 1024 * 1024) {
      files = undefined; fileBytes = 0;
      question += '\n[Вложения предыдущего сообщения не включены в контекст по лимиту.]';
    }
    if (turn.status === 'incomplete' || turn.status === 'partial' || turn.status === 'awaiting_confirmation') {
      answer = turn.status === 'awaiting_confirmation' ? 'В предыдущем запросе было подготовлено действие; его текущее состояние требует отдельной проверки.' : 'Предыдущий ответ получен не полностью и не подтверждает текущее состояние.';
      if (turn.request_id && /^[a-f0-9]{32}$/.test(turn.request_id)) answer += ' Журнал запроса ИИ: ' + turn.request_id + '.';
    }
    if (length + question.length + answer.length > 48000) break;
    length += question.length + answer.length;
    bytes += fileBytes;
    result.unshift({ role: 'user', content: question, attachments: files?.map(({ name, mime, data }) => ({ name, mime, data })) }, { role: 'assistant', content: answer });
  }
  return result;
}
