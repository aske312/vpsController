"""Private, bounded conversation cache. Provider credentials never enter it."""
import re
import time
import uuid

from fastapi import HTTPException
import application_operation as operations

MAX_BYTES = 5 * 1024 * 1024 * 1024
MAX_CONVERSATIONS = 10


def folder(api, actor='panel', channel='panel'):
    if channel not in ('panel', 'bot') or not re.fullmatch(r'panel|[1-9][0-9]{0,15}', str(actor)):
        raise HTTPException(422, 'Некорректный контур диалога')
    return api.DATA_DIR / 'assistant' / 'conversations' / (channel + '-' + str(actor))


def path(api, identity, actor='panel', channel='panel'):
    if not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise HTTPException(422, 'Некорректный ID диалога')
    return folder(api, actor, channel) / (identity + '.json')


def _read(file):
    if file.exists() and file.stat().st_size > 16 * 1024 * 1024:
        raise HTTPException(409, 'Кеш диалога превышает допустимый размер')
    try:
        record = operations.read(file)
    except operations.OperationConflict:
        raise HTTPException(409, 'Кеш диалога повреждён; файл сохранён для восстановления') from None
    if not record:
        if file.exists():
            raise HTTPException(409, 'Кеш диалога пуст или повреждён')
        return {}
    if (not isinstance(record.get('turns'), list) or len(record['turns']) > 40 or record.get('schema') != 1
            or not isinstance(record.get('title'), str) or not isinstance(record.get('updated_at'), str)
            or record.get('id') != file.stem):
        raise HTTPException(409, 'Формат кеша диалога не поддерживается')
    for turn in record['turns']:
        if (not isinstance(turn, dict) or type(turn.get('id')) is not int or not isinstance(turn.get('question'), str)
                or not isinstance(turn.get('answer'), str) or not isinstance(turn.get('model'), str)
                or not isinstance(turn.get('files'), list) or not isinstance(turn.get('tools'), list)):
            raise HTTPException(409, 'Сообщение в кеше повреждено')
        if any(not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ('name', 'mime', 'data', 'id')) or type(item.get('size')) is not int for item in turn['files']):
            raise HTTPException(409, 'Вложение в кеше повреждено')
    return record


def get(api, identity, actor='panel', channel='panel', *, settings=None):
    with operations.locked(api.DATA_DIR):
        file = path(api, identity, actor, channel)
        if settings is not None and file.exists() and file.stat().st_mtime < time.time() - settings.get('memory_days', 30) * 86400:
            file.unlink()
        record = _read(file)
    if not record:
        raise HTTPException(404, 'Диалог не найден. Создайте новый чат')
    return record


def _prune(api, settings):
    import assistant_personalization
    assistant_personalization.expire(api, settings)
    root = api.DATA_DIR / 'assistant' / 'conversations'
    cutoff = time.time() - settings.get('memory_days', 30) * 86400
    files = sorted(root.glob('*/*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    counts, total = {}, sum(file.stat().st_size for file in (api.DATA_DIR / 'assistant' / 'personalization').glob('*.json'))
    total += sum(file.stat().st_size for file in (api.DATA_DIR / 'assistant' / 'bot-history').glob('*/*.json'))
    for file in files:
        info = file.stat()
        scope = file.parent.name
        counts[scope] = counts.get(scope, 0) + 1
        if info.st_mtime < cutoff or counts[scope] > MAX_CONVERSATIONS or total + info.st_size > MAX_BYTES:
            file.unlink()
        else:
            total += info.st_size


def listing(api, settings, actor='panel', channel='panel'):
    if not settings.get('memory_enabled', True):
        return {'enabled': False, 'items': []}
    with operations.locked(api.DATA_DIR):
        _prune(api, settings)
        items = []
        for file in folder(api, actor, channel).glob('*.json'):
            record = _read(file)
            if record:
                items.append({key: record[key] for key in ('id', 'title', 'updated_at') } | {'turns': len(record['turns'])})
    return {'enabled': True, 'items': sorted(items, key=lambda item: item['updated_at'], reverse=True)}


def append(api, identity, actor, channel, question, files, result, model, settings, *, request_id=None):
    with operations.locked(api.DATA_DIR):
        file = path(api, identity, actor, channel)
        record = _read(file) or {'schema': 1, 'id': identity, 'title': (question.strip().splitlines() or [files[0]['name'] if files else 'Новый диалог'])[0][:80], 'turns': []}
        attachments = [{**item, 'id': uuid.uuid4().hex, 'size': len(item['data']) * 3 // 4 - (len(item['data']) - len(item['data'].rstrip('=')))} for item in files]
        identity_turn = max((turn['id'] for turn in record['turns']), default=0) + 1
        record['turns'].append({'id': identity_turn, 'question': question, 'at': operations.timestamp(), 'files': attachments, 'answer': result['answer'], 'tools': result.get('tools_used', []), 'model': model})
        if isinstance(request_id, str) and re.fullmatch(r'[a-f0-9]{32}', request_id):
            record['turns'][-1]['request_id'] = request_id
            # Retain the same wall-clock duration shown by the durable chat job,
            # including queue time, when the dialogue is reopened later.
            request = operations.read(api.DATA_DIR / 'assistant' / 'requests' / (request_id + '.json'))
            started = request.get('created_at')
            now = time.time()
            if (request.get('actor') == actor and request.get('channel', 'panel') == channel
                    and type(started) in (int, float) and 0 < started <= now):
                record['turns'][-1]['duration_ms'] = round((now - started) * 1000)
        if result.get('status') in ('incomplete', 'partial', 'awaiting_confirmation'):
            record['turns'][-1]['status'] = result['status']
        record['turns'] = record['turns'][-40:]
        file_bytes = 0
        for turn in reversed(record['turns']):
            count = sum(len(item['data']) for item in turn.get('files', []))
            if file_bytes + count > 8400000:
                turn['files'] = []
                turn['files_expired'] = True
            else:
                file_bytes += count
        record['updated_at'] = operations.timestamp()
        file.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        file.parent.parent.chmod(0o700)
        file.parent.mkdir(mode=0o700, exist_ok=True)
        file.parent.chmod(0o700)
        operations.atomic_json(file, record)
        _prune(api, settings)
    return record


def context(record, current_files):
    result, length = [], 0
    encoded = sum(len(item['data']) for item in current_files)
    for turn in reversed(record.get('turns', [])[-20:]):
        question, answer = turn['question'][:8000] or 'Файлы', turn['answer'][:8000]
        files = turn.get('files', [])
        amount = sum(len(item['data']) for item in files)
        if encoded + amount > 8400000:
            files, amount = [], 0
            question += '\n[Вложения предыдущего сообщения не включены в контекст по лимиту.]'
        if turn.get('status') in ('incomplete', 'partial', 'awaiting_confirmation'):
            answer = ('Предыдущий ответ получен не полностью и не подтверждает текущее состояние.'
                      if turn['status'] != 'awaiting_confirmation' else 'В предыдущем запросе было подготовлено действие; его текущее состояние требует отдельной проверки.')
            if isinstance(turn.get('request_id'), str) and re.fullmatch(r'[a-f0-9]{32}', turn['request_id']):
                answer += ' Журнал запроса ИИ: ' + turn['request_id'] + '.'
        if length + len(question) + len(answer) > 48000:
            break
        length += len(question) + len(answer)
        encoded += amount
        result[0:0] = [{'role': 'user', 'content': question, 'attachments': [{key: item[key] for key in ('name', 'mime', 'data')} for item in files]}, {'role': 'assistant', 'content': answer}]
    return result


def merge_command_context(stored, provided, names, sanitize):
    """Insert command events between trusted AI turns, never after newer turns."""
    records = [stored[index:index + 2] for index in range(0, len(stored) - 1, 2)]
    client = [provided[index:index + 2] for index in range(0, len(provided) - 1, 2)
              if provided[index]['role'] == 'user' and provided[index + 1]['role'] == 'assistant']
    def command(pair):
        text = pair[0]['content'].strip()
        return bool(text and (text.split()[0].lower() in names or text.startswith((
            'Подтверждено:', 'Отклонено:', 'Попытка подтверждения:', 'Попытка отказа:'))))
    anchors, cursor = {}, len(records) - 1
    for index in reversed(range(len(client))):
        if command(client[index]): continue
        question = client[index][0]['content']
        match = next((position for position in range(cursor, -1, -1)
                      if records[position][0]['content'] == question
                      or records[position][0]['content'].startswith(question + '\n[Вложения')), None)
        if match is not None:
            anchors[index], cursor = match, match - 1
    buckets = [[] for _ in range(len(records) + 1)]
    seen = {(pair[0]['content'], pair[1]['content']) for pair in records}
    for index, pair in enumerate(client):
        if not command(pair): continue
        signature = (pair[0]['content'], pair[1]['content'])
        if signature in seen: continue
        following = next((anchors[position] for position in range(index + 1, len(client)) if position in anchors), None)
        preceding = next((anchors[position] for position in range(index - 1, -1, -1) if position in anchors), None)
        # Undated references precede the known server timeline, rather than
        # presenting a possibly old confirmation as the newest user intent.
        unanchored = following is None and preceding is None and records
        target = following if following is not None else preceding + 1 if preceding is not None else 0
        question = pair[0]['content']
        if question.strip().split()[0].lower() in ('/confirm', '/cancel'):
            question = question.strip().split()[0] + ' [код скрыт]'
        if unanchored:
            question = '[Справочный результат команды; её время относительно диалога не подтверждено.]\n' + question
        buckets[target].extend([{'role': 'user', 'content': sanitize(question)},
                                {'role': 'assistant', 'content': sanitize(pair[1]['content'])}])
        seen.add(signature)
    merged = []
    for index, record in enumerate(records): merged.extend([*buckets[index], *record])
    merged.extend(buckets[-1])
    merged = merged[-40:]
    while sum(len(row['content']) for row in merged) > 48000: del merged[:2]
    return merged


def remove(api, identity, actor='panel'):
    with operations.locked(api.DATA_DIR):
        file = path(api, identity, actor)
        if not file.exists():
            raise HTTPException(404, 'Диалог не найден')
        file.unlink()
    return {'deleted': True}
