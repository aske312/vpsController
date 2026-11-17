"""Standalone NoderaBot: Bot API and optional calls to the installed AI-agent.

This runtime does not import MCP/Telethon or execute an AI tool loop.
"""
import asyncio
import base64
import html
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import sys
import tempfile
import time

import httpx

if not Path(__file__).with_name('assistant_messages.py').is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'api'))
from assistant_messages import notice, styled_message

logging.basicConfig(level=logging.ERROR)
logging.getLogger('httpx').setLevel(logging.ERROR)


def telegram_html(text):
    output = []
    for part in re.split(r'(```[^\n]*\n[\s\S]*?```|`[^`\n]+`)', text):
        if part.startswith('```') and part.endswith('```'):
            code = part[3:-3].split('\n', 1)[-1]
            output.append('<pre><code>' + html.escape(code) + '</code></pre>')
        elif part.startswith('`') and part.endswith('`'):
            output.append('<code>' + html.escape(part[1:-1]) + '</code>')
        else:
            escaped = html.escape(part)
            escaped = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', escaped)
            escaped = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', escaped)
            escaped = re.sub(r'(?m)^#{1,6}\s+(.+)$', r'<b>\1</b>', escaped)
            output.append(re.sub(r'(?m)^[-*+]\s+', '• ', escaped))
    return ''.join(output)


def telegram_chunks(text):
    chunks, current, stack = [], '', []
    units = 0
    for token in re.findall(r'<[^>]+>|&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);|.', telegram_html(text[:12000]), re.DOTALL):
        tag = token.startswith('<')
        size = 0 if tag else len(html.unescape(token).encode('utf-16-le')) // 2
        if units + size > 3500:
            chunks.append(current + ''.join('</' + name + '>' for name in reversed(stack)))
            current = ''.join('<' + name + '>' for name in stack); units = 0
        current += token; units += size
        if tag:
            if token.startswith('</'):
                if stack: stack.pop()
            else: stack.append(token[1:-1])
    if units:
        chunks.append(current + ''.join('</' + name + '>' for name in reversed(stack)))
    return chunks

PUBLIC_COMMANDS = [('id', 'Мой Telegram ID')]
MEMBER_COMMANDS = [('start', 'Приветствие'), ('help', 'Справка')] + PUBLIC_COMMANDS
SUBSCRIPTION_COMMANDS = MEMBER_COMMANDS + [('subscription', 'Моя подписка Mihomo'), ('profile', 'Параметры моей подписки'),
    ('devices', 'Устройства подписки'), ('traffic', 'Трафик подписки'), ('connect', 'Как подключиться')]
CHAT_COMMANDS = MEMBER_COMMANDS + [('models', 'Каталог моделей AI'), ('model', 'Текущая модель AI'), ('ai', 'Вопрос AI-ассистенту')]
ADMIN_COMMANDS = SUBSCRIPTION_COMMANDS + [('models', 'Доступные модели AI'), ('model', 'Текущая модель AI')] + [
    ('status', 'Состояние сервера'), ('resources', 'CPU, память, диск и трафик'),
    ('services', 'Состояние служб'), ('components', 'Установленные компоненты'), ('profiles', 'Доступные профили Mihomo'),
    ('network', 'Состояние сети'), ('security', 'Состояние безопасности'), ('logs', 'Последние логи'),
    ('diagnostics', 'Пассивная диагностика'), ('ai', 'Вопрос AI-ассистенту')]
TYPING_INTERVAL = 4
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 6 * 1024 * 1024
FILE_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp',
              'pdf': 'application/pdf', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              **{extension: 'text/plain' for extension in ('txt', 'md', 'csv', 'json', 'log', 'yaml', 'yml', 'xml')}}


class AttachmentError(ValueError):
    pass


def message_file(message):
    if message.get('photo'):
        photos = message['photo']
        candidates = [photo for photo in photos if type(photo.get('file_size')) is int and 0 < photo['file_size'] <= MAX_FILE_BYTES]
        # Missing size is checked against getFile and the bounded download.
        candidates = candidates or [photo for photo in photos if photo.get('file_size') is None]
        if not candidates: raise AttachmentError('Фото слишком большое. Лимит — 2 МБ на файл.')
        photo = max(candidates, key=lambda item: item.get('width', 0) * item.get('height', 0))
        return {'file_id': photo.get('file_id'), 'file_size': photo.get('file_size'), 'name': 'photo.jpg', 'mime': 'image/jpeg'}
    document = message.get('document') or {}
    name = document.get('file_name') or ''
    mime = FILE_TYPES.get(name.rsplit('.', 1)[-1].lower())
    if not mime:
        raise AttachmentError('Поддерживаются PNG, JPEG, WebP, PDF, DOCX и текстовые файлы: TXT, MD, CSV, JSON, LOG, YAML, XML.')
    if len(name) > 160 or re.search(r'[\x00-\x1f/\\]', name):
        raise AttachmentError('Недопустимое имя файла. Переименуйте файл и отправьте снова.')
    return {'file_id': document.get('file_id'), 'file_size': document.get('file_size'), 'name': name, 'mime': mime}


async def download_attachment(settings, message):
    file = message_file(message)
    if not isinstance(file['file_id'], str) or not 0 < len(file['file_id']) <= 512:
        raise AttachmentError('Telegram не передал идентификатор файла. Отправьте его снова.')
    def check_size(size):
        if size is not None and (type(size) is not int or not 0 < size <= MAX_FILE_BYTES):
            raise AttachmentError('Файл слишком большой или пустой. Лимит — 2 МБ на файл.')
    check_size(file['file_size'])
    metadata = await bot_api(settings, 'getFile', file_id=file['file_id'])
    check_size(metadata.get('file_size'))
    path = metadata.get('file_path', '')
    # Fixed Telegram origin; never follow redirects or accept remote URLs,
    # traversal, queries or percent-encoded paths from the transport response.
    if not isinstance(path, str) or not re.fullmatch(r'(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+', path) or path.rsplit('/', 1)[-1] in ('.', '..'):
        raise AttachmentError('Не удалось получить безопасный путь файла из Telegram.')
    async with httpx.AsyncClient(timeout=40, trust_env=False, follow_redirects=False) as client:
        async with client.stream('GET', f"https://api.telegram.org/file/bot{settings['bot_token']}/{path}") as response:
            if response.status_code != 200: raise AttachmentError('Не удалось скачать файл из Telegram. Отправьте его снова.')
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(data) + len(chunk) > MAX_FILE_BYTES: raise AttachmentError('Файл слишком большой. Лимит — 2 МБ на файл.')
                data.extend(chunk)
    if not data: raise AttachmentError('Файл пустой.')
    mime = file['mime']
    valid = (data.startswith(b'\x89PNG\r\n\x1a\n') if mime == 'image/png' else
             data.startswith(b'\xff\xd8\xff') if mime == 'image/jpeg' else
             data[:4] == b'RIFF' and data[8:12] == b'WEBP' if mime == 'image/webp' else
             data.startswith(b'%PDF-') if mime == 'application/pdf' else
             data.startswith(b'PK\x03\x04') if mime == FILE_TYPES['docx'] else True)
    if not valid: raise AttachmentError('Содержимое файла не соответствует его формату.')
    if mime == 'text/plain':
        try:
            if '\x00' in data.decode('utf-8-sig'): raise UnicodeError()
        except UnicodeError:
            raise AttachmentError('Текстовый файл должен быть в кодировке UTF-8 и не содержать двоичных данных.') from None
    return {'name': file['name'], 'mime': mime, 'data': base64.b64encode(data).decode('ascii')}


async def attachment_reply(settings, sender, prompt, messages, access):
    if not access: return ''
    if access == 'subscription': return 'Для анализа файлов нужен доступ к AI-ассистенту. Команды подписки доступны: /help.'
    if len(messages) > 4: return 'Отправьте не более 4 файлов за один запрос. Каждый — до 2 МБ, вместе — до 6 МБ.'
    try:
        async with typing(settings, sender['id']):
            files, total = [], 0
            for message in messages:
                file = await download_attachment(settings, message)
                total += len(base64.b64decode(file['data']))
                if total > MAX_TOTAL_FILE_BYTES: raise AttachmentError('Общий размер файлов превышает 6 МБ.')
                files.append(file)
            if prompt.split(maxsplit=1) and prompt.split(maxsplit=1)[0].split('@')[0].lower() == '/ai':
                prompt = prompt.split(maxsplit=1)[1] if len(prompt.split(maxsplit=1)) > 1 else ''
            return await agent_request(settings, sender['id'], prompt or 'Проанализируй приложенные файлы.', attachments=files)
    except AttachmentError as exc:
        return str(exc)
    except Exception:
        return 'Не получилось обработать вложения. Проверьте формат и повторите отправку. Для изображений выберите модель с поддержкой зрения.'


# Full-message matches only: mentioning a resource in an analytical question
# must not replace that question with a canned answer or execute an action.
DIRECT_PHRASES = {
    '/help': ('помощь', 'справка', 'что ты умеешь', 'что умеет бот', 'что умеешь', 'список команд', 'команды', 'help'),
    '/hello': ('привет', 'здравствуй', 'здравствуйте', 'добрый день', 'доброе утро', 'добрый вечер', 'hello', 'hi'),
    '/thanks': ('спасибо', 'спасибо большое', 'благодарю', 'thanks', 'thank you'),
    '/id': ('мой id', 'мой telegram id', 'мой телеграм id', 'какой у меня id', 'узнать мой id'),
    '/status': ('сервер', 'статус', 'статус сервера', 'состояние сервера', 'как сервер', 'сервер работает', 'server status', 'uptime', 'аптайм'),
    '/resources': ('ресурсы', 'ресурсы сервера', 'нагрузка', 'нагрузка сервера', 'память', 'свободная память', 'место на диске', 'свободное место', 'cpu', 'ram', 'диск'),
    '/services': ('службы', 'сервисы', 'состояние служб', 'статус служб', 'список служб', 'какие службы работают', 'services'),
    '/components': ('компоненты', 'установленные компоненты', 'список компонентов', 'что установлено', 'components'),
    '/subscription': ('моя подписка', 'подписка', 'ссылка на подписку', 'моя ссылка', 'ссылка mihomo', 'ссылка на vpn', 'subscription'),
    '/profile': ('мой профиль', 'параметры подписки', 'лимиты подписки', 'срок подписки', 'когда истекает подписка', 'квота подписки'),
    '/profiles': ('профили', 'список профилей', 'профили mihomo'),
    '/devices': ('мои устройства', 'устройства подписки', 'список устройств', 'устройства'),
    '/traffic': ('мой трафик', 'трафик', 'трафик подписки', 'сколько трафика использовано', 'расход трафика'),
    '/connect': ('как подключиться', 'как подключить vpn', 'как подключить mihomo', 'как импортировать подписку', 'как обновить подписку'),
    '/models': ('модели', 'модели ai', 'список моделей', 'доступные модели', 'какие модели доступны'),
    '/model': ('текущая модель', 'какая модель выбрана', 'какая сейчас модель'),
    '/model default': ('ai по умолчанию', 'модель по умолчанию'),
    '/network': ('сеть', 'состояние сети', 'статус сети', 'сетевые настройки'),
    '/security': ('безопасность', 'состояние безопасности', 'статус безопасности'),
    '/diagnostics': ('диагностика', 'диагностика сервера', 'пассивная диагностика'),
    '/logs': ('логи', 'журнал приложения', 'логи приложения', 'последние логи'),
}
DIRECT_COMMANDS = {phrase: command for command, phrases in DIRECT_PHRASES.items() for phrase in phrases}
SUBSCRIPTION_READS = ('/subscription', '/profile', '/devices', '/traffic', '/connect')


def direct_prompt(prompt):
    text = ' '.join(prompt.strip().casefold().replace('ё', 'е').split())
    if not text or text.startswith('/'): return prompt.strip()
    text = text.strip(' .,!?:;')
    text = re.sub(r'^(?:пожалуйста[, ]+|можешь\s+|можно\s+)?(?:покажи|показать|скажи|дай|пришли|отправь)\s+', '', text)
    text = re.sub(r'[, ]+пожалуйста$', '', text)
    if text in DIRECT_COMMANDS: return DIRECT_COMMANDS[text]
    # Only explicit, bounded syntax is accepted for parameterized commands.
    for pattern, command in (
        (r'(?:перезапусти|перезапустить|restart) (?:службу |сервис )?([a-z0-9_-]{1,80})', '/service {} restart'),
        (r'(?:запусти|запустить|start) (?:службу |сервис )?([a-z0-9_-]{1,80})', '/service {} start'),
        (r'(?:останови|остановить|stop) (?:службу |сервис )?([a-z0-9_-]{1,80})', '/service {} stop'),
        (r'(?:результат операции|статус операции|операция) ([0-9a-f]{32})', '/operation {}'),
        (r'(?:логи|журнал) ([a-z0-9_-]{1,80})', '/logs {}'),
    ):
        match = re.fullmatch(pattern, text)
        if match: return command.format(match[1])
    return prompt.strip()


@asynccontextmanager
async def typing(settings, actor):
    async def keep_alive():
        while True:
            started = time.monotonic()
            try:
                await asyncio.wait_for(bot_api(settings, 'sendChatAction', chat_id=actor, action='typing'), timeout=3)
            except Exception:
                pass  # A cosmetic status must never discard or delay an AI answer.
            await asyncio.sleep(max(0.01, TYPING_INTERVAL - (time.monotonic() - started)))
    task = asyncio.create_task(keep_alive(), name=f'nodera-typing-{actor}')
    try:
        await asyncio.sleep(0)
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def command_menu(settings, actor=None, admin=False, chat=False, subscription=False):
    scope = {'type': 'chat', 'chat_id': actor} if actor else {'type': 'all_private_chats'}
    await bot_api(settings, 'setMyCommands', scope=scope,
                  commands=[{'command': name, 'description': description} for name, description in (ADMIN_COMMANDS if admin else (CHAT_COMMANDS + (SUBSCRIPTION_COMMANDS[len(MEMBER_COMMANDS):] if subscription else [])) if chat else SUBSCRIPTION_COMMANDS if subscription else PUBLIC_COMMANDS)])


async def server_command(settings, actor, prompt):
    if len(prompt) > 256:
        return 'Команда слишком длинная. /help — формат команд.'
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-command',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)},
            json={'command': prompt})
        if response.status_code != 200:
            if response.status_code in (401, 403):
                return 'Недостаточно прав для этого действия.'
            if response.status_code in (404, 409):
                detail = response.json().get('detail')
                if isinstance(detail, str): return detail[:1000]
            return 'Команда не выполнена. Проверьте состояние в панели; если подтверждение уже отправлено, используйте /operation с ID операции.'
        return response.json()['text']


async def remember_message(settings, message, role):
    if not settings.get('control_token') or not isinstance(message, dict): return
    actor, identity = message.get('chat', {}).get('id'), message.get('message_id')
    if type(actor) is not int or actor <= 0 or type(identity) is not int or identity <= 0: return
    sender = message.get('from', {})
    if role == 'user' and (sender.get('id') != actor or sender.get('is_bot')): return
    text = message.get('text') or message.get('caption') or ''
    if not text and (message.get('photo') or message.get('document')):
        text = '[Вложение: ' + str((message.get('document') or {}).get('file_name') or 'изображение')[:200] + ']'
    if not text: return
    body = {'message_id': identity, 'role': role, 'text': text[:12000], 'date': message.get('date', int(time.time()))}
    reply = message.get('reply_to_message', {})
    if type(reply.get('message_id')) is int and reply['message_id'] > 0: body['reply_to_message_id'] = reply['message_id']
    if (role == 'user' and reply.get('from', {}).get('is_bot')
            and str(reply.get('from', {}).get('id')) == settings['bot_token'].split(':')[0]
            and reply.get('chat', {}).get('id') == actor):
        body['reply_to_text'] = (reply.get('text') or reply.get('caption') or '')[:8000]
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.post('http://127.0.0.1:8000/api/assistant/bot-history/message', json=body,
                headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor),
                         'X-Nodera-Bot': hashlib.sha256(settings['bot_token'].encode()).hexdigest()})
            if response.status_code != 200: logging.error('NoderaBot history could not be saved')
    except Exception: logging.error('NoderaBot history could not be saved')


async def remember_incoming(settings, message):
    await remember_message(settings, message, 'user')
    quoted = message.get('reply_to_message') or {}
    if (quoted.get('from', {}).get('is_bot') and str(quoted.get('from', {}).get('id')) == settings['bot_token'].split(':')[0]
            and quoted.get('chat', {}).get('id') == message.get('chat', {}).get('id')):
        await remember_message(settings, quoted, 'assistant')


async def bot_api(settings, method, **payload):
    async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
        response = await client.post(f"https://api.telegram.org/bot{settings['bot_token']}/{method}", json=payload)
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError('Telegram Bot API вернул HTTP ' + str(response.status_code))
        result = response.json()['result']
    if method == 'sendMessage': await remember_message(settings, result, 'assistant')
    return result


async def agent_request(settings, actor, prompt, attachments=None, alert=None):
    headers = {'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)}
    request_id = secrets.token_hex(16)
    async with httpx.AsyncClient(timeout=960, trust_env=False) as client:
        payload = {'prompt': prompt, 'request_id': request_id}
        if attachments: payload['attachments'] = attachments
        if alert: payload['alert'] = alert
        task = asyncio.create_task(client.post('http://127.0.0.1:8000/api/assistant/bot-chat', headers=headers, json=payload))
        notified = False
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=3)
                if not task.done() and not notified:
                    try:
                        queue = await client.get('http://127.0.0.1:8000/api/assistant/bot-queue', headers=headers, params={'request_id': request_id}, timeout=5)
                        if queue.status_code == 200 and queue.json().get('state') == 'queued':
                            await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Запрос в очереди AI', 'Дождитесь ответа; повторять сообщение не нужно.', '⏳')), parse_mode='HTML')
                            notified = True
                    except Exception: pass
            response = await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError): await task
        if response.status_code != 200:
            if alert and response.status_code in (403, 404, 429):
                detail = response.json().get('detail')
                if isinstance(detail, str): return detail[:1000]
            if response.status_code in (429, 503):
                return 'Очередь AI занята или время ожидания истекло. Дождитесь ответа на предыдущие сообщения и попробуйте позже.'
            if response.status_code == 409:
                return 'Сейчас я не могу подготовить ответ. Информационные команды доступны; владелец может проверить настройки AI.'
            if attachments and response.status_code == 422:
                return 'Не удалось прочитать вложение. Поддерживаются PDF с текстом до 30 страниц, DOCX, UTF-8 текст и PNG/JPEG/WebP. Для скана отправьте изображение.'
            return 'Не получилось подготовить ответ. Попробуйте немного позже.'
        value = response.json()
        answer = value['result']['answer']
        pending_items = value.get('pending_actions') or ([value['pending_action']] if value.get('pending_action') else [])
        for pending in pending_items:
            action = {'start': 'Запуск', 'stop': 'Остановка', 'restart': 'Перезапуск'}.get(pending['action'], pending['action'])
            path = pending['service'].removeprefix('Mihomo ')
            impact = 'Изменения ожидают подтверждения.'
            if path == '/profiles' and pending['action'] == 'POST': action = 'Создание подписки Mihomo'
            elif path.endswith('/subscription/rotate'):
                action = 'Перевыпуск ссылки подписки'
                impact = 'После подтверждения старый URL и ссылки устройств перестанут работать. Подключения, устройства, правила и лимиты сохранятся. Новую ссылку нужно импортировать в клиент.'
            elif re.fullmatch(r'/profiles/[A-Za-z0-9_-]+', path):
                action = 'Удаление подписки Mihomo' if pending['action'] == 'DELETE' else 'Изменение подписки Mihomo'
            answer += '\n\n' + notice('Нужно ваше решение', f"**Действие:** {action}\n**Объект:** `{pending['service']}`\n\n{impact}", '✋')
            if pending.get('changes'):
                answer += '\n\n**Детали изменений**\n```json\n' + json.dumps(pending['changes'], ensure_ascii=False, indent=2)[:4000] + '\n```'
            answer += f"\n\n`/confirm {pending['code']}` — подтвердить\n`/cancel {pending['code']}` — отклонить"
        return answer


QUEUE_NOTICE = 'Сообщение принято в очередь. Сначала отвечу на ваши предыдущие вопросы.'
BUSY_NOTICE = 'Слишком много сообщений. Дождитесь ответа на предыдущий вопрос; это сообщение не принято в очередь.'


class MessageDispatcher:
    """Bounded parallel users with strict ordering inside each user's dialog."""
    def __init__(self, capacity=16, per_actor=3):
        self.capacity, self.per_actor = capacity, per_actor
        self.tasks, self.pending, self.locks = set(), {}, {}

    def submit(self, actor, action):
        if len(self.tasks) >= self.capacity or self.pending.get(actor, 0) >= self.per_actor: return False
        self.pending[actor] = self.pending.get(actor, 0) + 1
        lock = self.locks.setdefault(actor, asyncio.Lock())
        async def run():
            try:
                async with lock: await action()
            except asyncio.CancelledError: raise
            except Exception: logging.error('NoderaBot message processing or delivery failed')
            finally:
                self.pending[actor] -= 1
                if not self.pending[actor]:
                    self.pending.pop(actor); self.locks.pop(actor, None)
        task = asyncio.create_task(run(), name=f'nodera-message-{actor}')
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def close(self):
        for task in list(self.tasks): task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        self.tasks.clear(); self.pending.clear(); self.locks.clear()


_profile_cache = {}


async def observed_profile(settings, sender):
    key = (hashlib.sha256(settings['bot_token'].encode()).hexdigest(), sender['id'])
    previous = _profile_cache.get(key)
    if previous and time.monotonic() - previous[0] < 3600:
        return previous[1]
    details = {}
    try:
        chat = await bot_api(settings, 'getChat', chat_id=sender['id'])
        if chat.get('id') == sender['id'] and chat.get('type') == 'private':
            details = {'bio': str(chat.get('bio', ''))[:1024], 'birthdate': chat.get('birthdate')}
    except Exception:
        return previous[1] if previous else {}
    if len(_profile_cache) >= 1000: _profile_cache.clear()
    _profile_cache[key] = (time.monotonic(), details)
    return details


async def identify(settings, sender):
    """The gateway reads current permissions; credential snapshots are not ACLs."""
    details = await observed_profile(settings, sender)
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-contact',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(sender['id'])},
            json={'telegram_id': sender['id'], 'username': sender.get('username', ''),
                  'name': sender.get('first_name', '')[:80], 'last_name': sender.get('last_name', '')[:80],
                  'language_code': sender.get('language_code', '')[:35], 'is_premium': sender.get('is_premium'),
                  **details, 'is_bot': sender.get('is_bot', False)})
        if response.status_code != 200:
            return False
        access = response.json()
        return True if access.get('admin') is True else ('chat-subscription' if access.get('subscription') else 'chat') if access.get('chat') is True else 'subscription' if access.get('subscription') else False


def public_reply(sender, command):
    if command == '/id':
        tag = '\nТег: @' + sender['username'] if sender.get('username') else ''
        return f"Ваш Telegram ID: {sender['id']}{tag}\nДля доступа передайте этот ID владельцу."
    return ''


def assistant_help(admin=False, subscription=False):
    text = ('Привет! Я Нодера, ваш AI-ассистент.\n\n' +
            ('Отвечаю на вопросы, помогаю с вашей подпиской Mihomo и доступными настройками. ' if subscription else 'Отвечаю на вопросы и помогаю с доступными вам задачами. ') +
            '\n\n**Как общаться**\nТиповые запросы выполняю без AI: можно писать «мой трафик», «моя подписка» или выбирать кнопки. Для вопроса к AI используйте `/ai <вопрос>`.\n\n'
            '**Фото и документы**\nДобавьте вопрос в подпись к файлу. Можно прислать до 4 файлов: 2 МБ каждый, 6 МБ суммарно. Для изображений нужна модель с поддержкой зрения.\n\n'
            'Команды:\n/id — ваш Telegram ID\n/models — доступные модели\n/model — текущая модель')
    if subscription: text += '\n/subscription — ваша подписка Mihomo\n/profile — параметры и лимиты\n/devices — устройства\n/traffic — трафик\n/connect — как подключиться'
    if admin:
        text += '\n/status — состояние сервера\n/resources — ресурсы\n/services — состояние служб\n/components — компоненты\n/profiles — профили Mihomo\n/network — сеть\n/security — безопасность\n/logs [служба] — последние логи\n/diagnostics — пассивная диагностика'
    return text


def informational_reply(command, answer):
    if answer.startswith('Неизвестная команда'):
        return 'Не узнала команду. Напишите, что хотите сделать, или откройте /help.'
    if command == '/services':
        rows = [line.split(' · ') for line in answer.splitlines()]
        states = [' · '.join(parts[:2]) for parts in rows if len(parts) == 3 and re.fullmatch(r'[a-zA-Z0-9_.:@-]{1,80}', parts[0])]
        return 'Состояние служб:\n' + '\n'.join(states) if states else 'Не удалось получить состояние служб. Попробуйте немного позже.'
    return answer


async def reply(settings, sender, prompt, access):
    prompt = direct_prompt(prompt)
    if not prompt: return ''
    command = prompt.split()[0].split('@')[0].lower()
    if not access: return public_reply(sender, command)
    if command in ('/id', '/userid'): return public_reply(sender, '/id')
    if access == 'subscription' and command in ('/start', '/help'):
        return ('Команды вашей подписки Mihomo работают без AI:\n'
                '/subscription — ссылка\n/profile — параметры, лимиты и срок\n'
                '/devices — устройства\n/traffic — учтённый трафик\n/connect — как подключиться\n/id — ваш Telegram ID.\n'
                'Можно писать обычным текстом: «мой трафик», «мои устройства», «моя подписка».')
    if access == 'subscription' and command not in (*SUBSCRIPTION_READS, '/confirm', '/cancel', '/operation'):
        return 'Доступна ваша подписка Mihomo: /subscription. AI-ассистент для этой учётной записи не подключён.'
    if command in ('/start', '/help'):
        return assistant_help(access is True, access in (True, 'chat-subscription'))
    if command == '/hello': return 'Здравствуйте! /help — возможности и команды. Типовые запросы выполняются без AI.'
    if command == '/thanks': return 'Пожалуйста!'
    if command == '/connect':
        return ('Подключение Mihomo:\n1. Получите свою ссылку: /subscription.\n'
                '2. В совместимом клиенте Mihomo выберите импорт подписки по URL и вставьте ссылку.\n'
                '3. Обновите подписку, выберите профиль и включите подключение.\n'
                'После изменения настроек в панели обновите подписку в клиенте. Не передавайте ссылку другим людям.')
    if command == '/ai':
        prompt = prompt.split(maxsplit=1)[1].strip() if len(prompt.split(maxsplit=1)) > 1 else ''
        if not prompt: return 'Формат: /ai <вопрос>. Типовые запросы и кнопки работают без AI.'
        command = ''
    if command.startswith('/'):
        if access in ('chat', 'chat-subscription') and command not in ('/models', '/model', *SUBSCRIPTION_READS, '/confirm', '/cancel', '/operation'):
            return 'Вам доступен чат. /help — доступные команды.'
        try:
            return informational_reply(command, await server_command(settings, sender['id'], prompt))
        except Exception:
            return 'Не удалось получить результат команды. Проверьте панель или /operation с ID операции. Не повторяйте изменение, пока не проверите его результат.'
    try:
        async with typing(settings, sender['id']):
            return await agent_request(settings, sender['id'], prompt)
    except Exception:
        return 'Сейчас не получилось подготовить ответ. Попробуйте немного позже.'


BUTTON_COMMANDS = {'Моя подписка': '/subscription', 'Сервер': '/status', 'Ресурсы': '/resources', 'Службы': '/services',
                   'Компоненты': '/components', 'Модели AI': '/models',
                   'Текущая модель': '/model', 'AI по умолчанию': '/model default', 'Помощь': '/help',
                   'Мой профиль': '/profile', 'Мои устройства': '/devices', 'Мой трафик': '/traffic',
                   'Как подключиться': '/connect', 'Сеть': '/network', 'Безопасность': '/security', 'Логи': '/logs', 'Диагностика': '/diagnostics'}


def reply_keyboard(access):
    rows = [['Модели AI', 'Текущая модель'], ['Помощь']]
    if access is True:
        rows = [['Сервер', 'Ресурсы'], ['Службы', 'Компоненты'], ['Сеть', 'Безопасность'], ['Логи', 'Диагностика']] + rows
    subscription_rows = [['Моя подписка', 'Мой профиль'], ['Мои устройства', 'Мой трафик'], ['Как подключиться']]
    if access == 'subscription': rows = subscription_rows + [['Помощь']]
    elif access in (True, 'chat-subscription'): rows = subscription_rows + rows
    if not access:
        return {'remove_keyboard': True}
    return {'keyboard': [[{'text': text} for text in row] for row in rows],
            'resize_keyboard': True, 'is_persistent': True, 'input_field_placeholder': 'Команды подписки Mihomo' if access == 'subscription' else 'Напишите вопрос Нодере…'}


def inline_keyboard(answer, prompt, access, actions):
    """Opaque callbacks are scoped to one actor; long model IDs stay server-side."""
    rows = []
    def button(text, command):
        key = secrets.token_hex(8)
        actions[key] = command
        return {'text': text[:60], 'callback_data': 'nd:' + key}
    command = prompt.split()[0].split('@')[0].lower()
    if access and command == '/models' and answer.startswith('Модели · страница '):
        for model in answer.split('\n\n', 1)[0].splitlines()[1:]:
            rows.append([button(model, '/model ' + model)])
        page = re.search(r'страница (\d+)/(\d+)', answer)
        if page:
            current, total = map(int, page.groups())
            navigation = []
            if current > 1: navigation.append(button('← Назад', f'/models {current - 1}'))
            if current < total: navigation.append(button('Далее →', f'/models {current + 1}'))
            if navigation: rows.append(navigation)
        rows.append([button('AI по умолчанию', '/model default')])
    confirmations = list(dict.fromkeys(re.findall(r'/confirm ([0-9a-f]{8})', answer)))
    if access:
        for index, code in enumerate(confirmations[:32], 1):
            rows.append([button(f'Подтвердить {index}' if len(confirmations) > 1 else 'Подтвердить действие', '/confirm ' + code), button(f'Отказать {index}' if len(confirmations) > 1 else 'Отказать', '/cancel ' + code)])
    operation = re.search(r'/operation ([0-9a-f]{32})', answer)
    if access and operation:
        rows.append([button('Проверить результат', '/operation ' + operation[1])])
    return {'inline_keyboard': rows} if rows else None


async def bot(settings):
    if not settings['modules']['nodera-bot'] or not settings.get('bot_token'):
        raise ValueError('Нужен токен включённого NoderaBot')
    if (await bot_api(settings, 'getWebhookInfo')).get('url'):
        raise ValueError('У бота установлен webhook; long polling не запущен')
    try: await command_menu(settings)
    except Exception: logging.error('NoderaBot command menu could not be updated')
    state_root = Path(os.environ.get('STATE_DIRECTORY', tempfile.gettempdir()))
    state_file = state_root / 'offset.json'
    offset = json.loads(state_file.read_text()).get('offset', 0) if state_file.exists() else 0
    last = {}
    menus = {}
    callbacks = {}
    media_groups = {}
    dispatcher = MessageDispatcher()
    notices = {}
    async def deliver(sender, prompt, messages=None, alert=None, context=None):
        actor = sender['id']
        try: access = await identify(settings, sender)
        except Exception: access = False
        if context: await remember_incoming(settings, context)
        if not prompt and not messages: return
        admin = access is True
        role = 'admin' if admin else str(access) if access else 'guest'
        if menus.get(actor) != role:
            try:
                await command_menu(settings, actor, admin, access in ('chat', 'chat-subscription'), access in (True, 'subscription', 'chat-subscription'))
                if len(menus) >= 1000: menus.clear()
                menus[actor] = role
            except Exception: logging.error('NoderaBot private command menu could not be updated')
        if alert:
            if access is not True:
                answer = 'Разбор алерта доступен владельцу с доступом к ИИ.'
            else:
                try:
                    async with typing(settings, actor):
                        answer = await agent_request(settings, actor, '', alert=alert)
                except Exception:
                    answer = 'Не удалось запустить разбор алерта. Проверьте доступность AI-ассистента и повторите позже.'
        else:
            answer = await attachment_reply(settings, sender, prompt, messages, access) if messages else await reply(settings, sender, prompt, access)
        if access:
            # A request can outlive a permission change.
            try: current_access = await identify(settings, sender)
            except Exception: current_access = False
            if current_access != access:
                access = False
                answer = public_reply(sender, '')
        if not answer: return
        if len(callbacks) >= 1000: callbacks.clear()
        actions = {}
        markup = inline_keyboard(answer, prompt or 'Вложения', access, actions)
        if actions: callbacks[actor] = actions
        answer = styled_message(direct_prompt(prompt or '') or '', answer)
        for chunk in telegram_chunks(answer):
            await bot_api(settings, 'sendMessage', chat_id=actor, text=chunk, parse_mode='HTML', reply_markup=reply_keyboard(access))
        if markup:
            await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Выберите действие', 'Нажмите на подходящий вариант ниже.', '👇')), parse_mode='HTML', reply_markup=markup)
    async def deliver_album(sender, key, album):
        # Telegram sends an album as separate updates. Coalesce a short burst
        # before downloading, so the album consumes one AI turn and one queue slot.
        await asyncio.sleep(1)
        if media_groups.get(key) is album: media_groups.pop(key)
        prompt = '\n'.join(row.get('caption', '').strip() for row in album if row.get('caption')).strip()
        for message in album: await remember_incoming(settings, message)
        await deliver(sender, prompt, album)
    try:
        while True:
            try:
                updates = await bot_api(settings, 'getUpdates', offset=offset, timeout=25, allowed_updates=['message', 'edited_message', 'callback_query'])
                for update in updates:
                    offset = max(offset, update['update_id'] + 1)
                    temporary = state_file.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'offset': offset})); temporary.chmod(0o600); temporary.replace(state_file)
                    callback = update.get('callback_query')
                    message = callback.get('message', {}) if callback else update.get('message', update.get('edited_message', {}))
                    sender = callback.get('from', {}) if callback else message.get('from', {})
                    actor = sender.get('id')
                    if message.get('chat', {}).get('type') != 'private' or type(actor) is not int or actor <= 0 or sender.get('is_bot'): continue
                    if update.get('edited_message'):
                        dispatcher.submit((actor, 'command'), lambda message=message: remember_incoming(settings, message))
                        continue  # Edits update context without executing commands again.
                    messages = None
                    alert = None
                    if callback:
                        await bot_api(settings, 'answerCallbackQuery', callback_query_id=callback['id'])
                        key = callback.get('data', '')
                        match = re.fullmatch(r'alert:([0-9a-f]{32}):(brief|deep)', key)
                        if match and message.get('chat', {}).get('id') == actor:
                            alert = {'token': match[1], 'mode': match[2]}
                            prompt = 'Разбор алерта с ИИ'
                        else:
                            prompt = callbacks.get(actor, {}).get(key[3:], '') if key.startswith('nd:') else ''
                        if not prompt:
                            await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Обновите меню', 'Это меню устарело. Откройте раздел кнопкой ниже.', '↻')), parse_mode='HTML')
                            continue
                    else:
                        messages = [message] if message.get('photo') or message.get('document') else None
                        prompt = (message.get('caption', '') if messages else message.get('text', '')).strip()
                        if not messages: prompt = direct_prompt(BUTTON_COMMANDS.get(prompt, prompt))
                    album_key = (actor, str(message['media_group_id'])) if messages and message.get('media_group_id') else None
                    if album_key in media_groups:
                        # Retain one overflow entry to reject oversized albums;
                        # never grow memory with an unbounded group of updates.
                        if len(media_groups[album_key]) < 5: media_groups[album_key].append(message)
                        continue
                    is_command = not messages and prompt.startswith('/') and prompt.split()[0].split('@')[0].lower() != '/ai'
                    if not is_command and time.monotonic() - last.get(actor, 0) < 3:
                        if time.monotonic() - notices.get(actor, 0) >= 10:
                            notices[actor] = time.monotonic()
                            await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Очередь заполнена', BUSY_NOTICE, '⏳')), parse_mode='HTML')
                        continue
                    if len(last) >= 1000:
                        last = {key: at for key, at in last.items() if time.monotonic() - at < 60}
                        if len(last) >= 1000: continue
                    if not is_command:
                        last[actor] = time.monotonic()
                    # Explicit commands remain responsive while this user's AI
                    # dialog waits; commands themselves keep their own ordering.
                    stream = (actor, 'command') if is_command else actor
                    if album_key:
                        media_groups[album_key] = messages
                        accepted = dispatcher.submit(stream, lambda sender=sender, key=album_key, album=messages: deliver_album(sender, key, album))
                        if not accepted: media_groups.pop(album_key, None)
                    else:
                        accepted = dispatcher.submit(stream, lambda sender=sender, prompt=prompt, messages=messages, alert=alert, message=message, callback=callback: deliver(sender, prompt, messages, alert, context=None if callback else message))
                    if (not accepted or dispatcher.pending.get(stream, 0) > 1) and time.monotonic() - notices.get(actor, 0) >= 10:
                        notices[actor] = time.monotonic()
                        if len(notices) > 1000: notices = {actor: time.monotonic()}
                        await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Сообщение в очереди' if accepted else 'Очередь заполнена', QUEUE_NOTICE if accepted else BUSY_NOTICE, '⏳')), parse_mode='HTML')
            except Exception:
                logging.error('NoderaBot polling or delivery failed')
                await asyncio.sleep(5)
    finally:
        await dispatcher.close()
        media_groups.clear()


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--self-check':
            print('NoderaBot runtime ready')
        elif sys.argv[1] == '--bot':
            path = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'settings'
            asyncio.run(bot(json.loads(path.read_text())))
        elif sys.argv[1] == '--check':
            payload = json.loads(sys.stdin.read(1024 * 1024)); settings = payload['settings']
            if not settings['modules']['nodera-bot']: raise ValueError('Bot disabled')
            result = asyncio.run(bot_api(settings, 'getMe'))
            print(json.dumps({'ok': True, 'result': {'username': result['username']}}))
        else: raise ValueError('Unknown command')
    except Exception:
        if sys.argv[1] == '--check': print(json.dumps({'ok': False, 'error': 'Не удалось проверить NoderaBot. Проверьте токен и доступ к Telegram.'}, ensure_ascii=False))
        else: logging.error('NoderaBot stopped')
        sys.exit(1)
