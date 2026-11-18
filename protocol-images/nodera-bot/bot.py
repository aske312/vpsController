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
from contextvars import ContextVar
from pathlib import Path
import sys
import tempfile
import time

import httpx

if not Path(__file__).with_name('assistant_messages.py').is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'api'))
from assistant_messages import notice

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

PUBLIC_COMMANDS = [('start', 'Начать знакомство'), ('myprofile', 'Мой профиль Nodera'), ('setup', 'Заполнить профиль'), ('id', 'Мой Telegram ID')]
MEMBER_COMMANDS = [('menu', 'Открыть меню'), ('help', 'Справка')] + PUBLIC_COMMANDS
SUBSCRIPTION_COMMANDS = MEMBER_COMMANDS + [('subscription', 'Моя подписка'), ('profile', 'Статус подписки'),
    ('devices', 'Устройства подписки'), ('connect', 'Как подключиться'), ('apps', 'Приложения для подключения')]
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


class DeliveryPending(RuntimeError):
    """Keep accepted work on disk until API admission/delivery can be reconciled."""


class TelegramAPIError(RuntimeError):
    def __init__(self, method, status):
        self.method = method
        self.status = status
        super().__init__(f'Telegram {method}: HTTP {status}')


class ContactBlocked(PermissionError):
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
    except DeliveryPending:
        raise
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
    '/apps': ('приложения', 'приложения для подключения', 'скачать клиент', 'скачать приложение'),
    '/telegram configure': ('как настроить telegram api', 'настроить telegram api', 'помоги настроить telegram api', 'настрой telegram api'),
    '/telegram session': ('как подключить telegram api', 'подключить telegram api'),
    '/telegram': ('telegram api', 'телеграм api'),
    '/models': ('модели', 'модели ai', 'список моделей', 'доступные модели', 'какие модели доступны'),
    '/model': ('текущая модель', 'какая модель выбрана', 'какая сейчас модель'),
    '/model default': ('ai по умолчанию', 'модель по умолчанию'),
    '/network': ('сеть', 'состояние сети', 'статус сети', 'сетевые настройки'),
    '/security': ('безопасность', 'состояние безопасности', 'статус безопасности'),
    '/diagnostics': ('диагностика', 'диагностика сервера', 'пассивная диагностика'),
    '/logs': ('логи', 'журнал приложения', 'логи приложения', 'последние логи'),
}
DIRECT_COMMANDS = {phrase: command for command, phrases in DIRECT_PHRASES.items() for phrase in phrases}
SUBSCRIPTION_READS = ('/subscription', '/profile', '/devices', '/traffic', '/connect', '/apps')


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
    items = available_commands() if actor and access_details.get() is not None else (ADMIN_COMMANDS if admin else (CHAT_COMMANDS + (SUBSCRIPTION_COMMANDS[len(MEMBER_COMMANDS):] if subscription else [])) if chat else SUBSCRIPTION_COMMANDS if subscription else PUBLIC_COMMANDS)
    await bot_api(settings, 'setMyCommands', scope=scope, commands=[{'command': name, 'description': description} for name, description in items])


async def server_command(settings, actor, prompt):
    if len(prompt) > 256:
        return 'Команда слишком длинная. /help — формат команд.'
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-command',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)},
            json={'command': prompt, **({'request_id':incoming_request.get()} if incoming_request.get() else {})})
        if response.status_code != 200:
            if response.status_code in (401, 403):
                return 'Недостаточно прав для этого действия.'
            if response.status_code in (404, 409, 422):
                detail = response.json().get('detail')
                if isinstance(detail, str): return detail[:1000]
            return 'Команда не выполнена. Проверьте состояние в панели; если подтверждение уже отправлено, используйте /operation с ID операции.'
        value = response.json()
        if prompt.split(maxsplit=1)[0].split('@')[0].lower() in ('/confirm','/cancel'):
            value['decision_complete'] = True
        if 'buttons' in value or 'document' in value or value.get('decision_complete'): command_view.set(value)
        return value['text']


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
    # Session strings must never enter history later supplied to a model.
    text = re.sub(r'(?<![A-Za-z0-9_=-])1[A-Za-z0-9_=-]{100,4095}(?![A-Za-z0-9_=-])', '[Telegram API: скрытые данные]', text)
    body = {'message_id': identity, 'role': role, 'text': text[:12000], 'date': message.get('date', int(time.time()))}
    reply = message.get('reply_to_message', {})
    if type(reply.get('message_id')) is int and reply['message_id'] > 0: body['reply_to_message_id'] = reply['message_id']
    if (role == 'user' and reply.get('from', {}).get('is_bot')
            and str(reply.get('from', {}).get('id')) == settings['bot_token'].split(':')[0]
            and reply.get('chat', {}).get('id') == actor):
        body['reply_to_text'] = (reply.get('text') or reply.get('caption') or '')[:8000]
        body['reply_to_text'] = re.sub(r'(?<![A-Za-z0-9_=-])1[A-Za-z0-9_=-]{100,4095}(?![A-Za-z0-9_=-])', '[Telegram API: скрытые данные]', body['reply_to_text'])
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
        if method == 'sendDocument':
            document = payload.pop('document')
            data = {key:json.dumps(value) if isinstance(value,dict) else str(value) for key,value in payload.items()}
            response = await client.post(f"https://api.telegram.org/bot{settings['bot_token']}/{method}", data=data,
                files={'document':(document['name'],document['text'].encode('utf-8'),'text/plain; charset=utf-8')})
        else:
            response = await client.post(f"https://api.telegram.org/bot{settings['bot_token']}/{method}", json=payload)
        if response.status_code != 200 or not response.json().get('ok'):
            raise TelegramAPIError(method, response.status_code)
        result = response.json()['result']
    if method in ('sendMessage','sendDocument'): await remember_message(settings, result, 'assistant')
    return result


async def agent_request(settings, actor, prompt, attachments=None, alert=None):
    headers = {'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)}
    request_id = incoming_request.get() or secrets.token_hex(16)
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        payload = {'prompt': prompt, 'request_id': request_id}
        if attachments: payload['attachments'] = attachments
        if alert: payload['alert'] = alert
        try:
            response = await client.post('http://127.0.0.1:8000/api/assistant/bot-requests', headers=headers, json=payload)
        except httpx.TransportError:
            raise DeliveryPending() from None
        if response.status_code in (429, 502, 503, 504): raise DeliveryPending()
        if response.status_code == 202:
            notified = False
            waiting_since = time.monotonic()
            while True:
                try:
                    response = await client.get('http://127.0.0.1:8000/api/assistant/bot-requests/' + request_id, headers=headers, timeout=10)
                except httpx.TransportError:
                    await asyncio.sleep(3); continue
                if response.status_code in (502, 503, 504):
                    await asyncio.sleep(3); continue
                if response.status_code != 200: break
                job = response.json()
                if job.get('state') == 'done':
                    response = httpx.Response(200, json=job['output']); break
                if job.get('state') in ('failed', 'unknown'):
                    return job.get('error', {}).get('message') or 'Результат запроса не подтверждён. Проверьте состояние перед повтором.'
                if job.get('state') == 'queued' and not notified and time.monotonic() - waiting_since >= 2:
                    try:
                        sent = await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Сообщение в очереди', QUEUE_NOTICE, '⏳')), parse_mode='HTML')
                        if transient_sink.get(): transient_sink.get()(actor, sent)
                        notified = True
                    except Exception: pass
                await asyncio.sleep(1)
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
        if (not pending_items and 'offer_telegram_setup' in value['result'].get('tools_used', [])
                and 'roles_manage' in (access_details.get() or {}).get('permissions', [])):
            command_view.set({'buttons': [[{'text': 'Настроить Telegram MCP', 'command': '/telegram configure'}]]})
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


incoming_request = ContextVar('nodera_incoming_request', default=None)
delivery_record = ContextVar('nodera_delivery_record', default=None)
profile_state = ContextVar('nodera_profile_state', default=None)
access_details = ContextVar('nodera_access_details', default=None)
command_view = ContextVar('nodera_command_view', default=None)

QUEUE_NOTICE = 'Сообщение принято в очередь. Сначала отвечу на ваши предыдущие вопросы.'
transient_sink = ContextVar('transient_sink', default=None)
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


class DurableInbox:
    """Small private Telegram envelopes survive polling and bot restarts."""
    def __init__(self, root):
        self.root = root / 'inbox'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.inflight, self.retry_at, self.offset = set(), {}, 0

    def read(self, key):
        return json.loads((self.root / (key + '.json')).read_text(encoding='utf-8'))

    def save(self, key, row):
        if not re.fullmatch(r'[a-z0-9-]{1,100}', key): raise ValueError('Invalid inbox key')
        path = self.root / (key + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        temporary.chmod(0o600); temporary.replace(path)

    def put(self, key, row):
        path = self.root / (key + '.json')
        if path.exists(): return True
        if len(list(self.root.glob('*.json'))) >= 256: return False
        self.save(key, row)
        return True

    def amend(self, key, **values):
        row = self.read(key); row.update(values); self.save(key, row)

    def acknowledge(self, offset):
        self.offset = offset
        for path in self.root.glob('*.json'):
            row = self.read(path.stem)
            if row.get('delivered') and row['order'] < offset: path.unlink(missing_ok=True)

    def waiting(self, actor, command=False):
        return sum(row.get('sender', {}).get('id') == actor and bool(row.get('command')) == command and not row.get('delivered')
                   for row in (self.read(path.stem) for path in self.root.glob('*.json')))

    def pump(self, dispatcher, deliver):
        rows = [(path.stem, json.loads(path.read_text(encoding='utf-8'))) for path in self.root.glob('*.json')]
        seen = set()
        for key, row in sorted(rows, key=lambda item: item[1]['order']):
            if row.get('delivered'): continue
            actor = row['sender']['id']
            stream = (actor, 'command') if row.get('command') else actor
            if stream in seen: continue
            seen.add(stream)
            if key in self.inflight or row.get('not_before', 0) > time.time() or self.retry_at.get(key, 0) > time.monotonic(): continue
            async def run(key=key):
                try:
                    try:
                        await deliver(self.read(key), key)
                    except TelegramAPIError as error:
                        if error.method not in ('sendMessage', 'sendDocument') or error.status not in (400, 403):
                            raise
                        logging.warning('NoderaBot delivery rejected: %s', error)
                    self.amend(key, delivered=True)
                    if self.read(key)['order'] < self.offset: (self.root / (key + '.json')).unlink(missing_ok=True)
                    self.retry_at.pop(key, None)
                except asyncio.CancelledError: raise
                except TelegramAPIError as error:
                    self.retry_at[key] = time.monotonic() + 3
                    logging.error('NoderaBot accepted message remains queued for delivery: %s', error)
                except Exception:
                    self.retry_at[key] = time.monotonic() + 3
                    logging.error('NoderaBot accepted message remains queued for delivery')
                finally: self.inflight.discard(key)
            if dispatcher.submit(stream, run): self.inflight.add(key)


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
            if response.status_code not in (401, 403): raise DeliveryPending()
            return False
        access = response.json()
        if access.get('blocked'): raise ContactBlocked()
        access_details.set(access if 'permissions' in access else None)
        profile_state.set(access.get('onboarding'))
        return True if access.get('admin') is True else ('chat-subscription' if access.get('subscription') else 'chat') if access.get('chat') is True else 'subscription' if access.get('subscription') else False


async def profile_reply(settings, actor, prompt):
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        try:
            response = await client.post('http://127.0.0.1:8000/api/assistant/bot-profile',
                headers={'Authorization':'Bearer ' + settings['control_token'], 'X-MCP-Actor':str(actor)},
                json={'text':prompt, 'request_id':incoming_request.get() or secrets.token_hex(16)})
        except httpx.TransportError: raise DeliveryPending() from None
        if response.status_code == 403: raise ContactBlocked()
        if response.status_code != 200: raise DeliveryPending()
        value = response.json()
        if value.get('text') is not None: command_view.set(value)
        return value.get('text')


COMMAND_GRANTS = {'subscription':'subscription_read','profile':'subscription_read','devices':'subscription_read','apps':'subscription_read','connect':'subscription_read',
    'models':'model_select','model':'model_select','ai':'chat','status':'server_read','resources':'server_read','services':'server_read','components':'server_read',
    'network':'network_read','security':'security_read','diagnostics':'diagnostics','logs':'logs_read','logfile':'logs_read','admin':'subscriptions_admin','service':'server_manage','telegram':'telegram_read'}


def available_commands():
    detail = access_details.get() or {}; sections = detail.get('sections', {}); grants = set(detail.get('permissions', []))
    items = list(PUBLIC_COMMANDS)
    if any(sections.values()):
        items = MEMBER_COMMANDS.copy()
    candidates = []
    if sections.get('subscription'): candidates += SUBSCRIPTION_COMMANDS[len(MEMBER_COMMANDS):]
    elif sections.get('subscription_setup'): candidates += [('subscription','Оформить подписку Mihomo')]
    if sections.get('telegram'): candidates += [('telegram','Telegram API')]
    if sections.get('ai'): candidates += CHAT_COMMANDS[len(MEMBER_COMMANDS):]
    if sections.get('server'): candidates += [(key,label) for key,label in ADMIN_COMMANDS if key in ('status','services','network','security','diagnostics','logs')]
    if sections.get('admin'): candidates += [('admin','Администрирование Mihomo')]
    items += [(key,label) for key,label in candidates if COMMAND_GRANTS.get(key) in grants]
    return list(dict(items).items())


def access_signature(access):
    detail = access_details.get()
    return json.dumps({key:detail.get(key) for key in ('permissions','products','linked_profile')} if detail else access,sort_keys=True)


async def scoped_reply(settings, sender, prompt, access):
    detail = access_details.get(); sections=detail.get('sections', {}); grants=set(detail.get('permissions', []))
    command=prompt.split()[0].split('@')[0].lower()
    if detail.get('telegram_dialog') and command in ('/menu','/_refresh_menu','/start','/help','/admin','/myprofile','/setup'):
        await server_command(settings,sender['id'],'/telegram cancel'); command_view.set(None)
    if detail.get('telegram_dialog') and not command.startswith('/'):
        if not re.fullmatch(r'1[A-Za-z0-9_=-]{100,4095}', prompt):
            command_view.set({'buttons': [[{'text': 'Отмена', 'command': '/telegram cancel'}]]})
            return 'Telegram API\nОтправьте только строку Telethon StringSession отдельным сообщением.'
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            try:
                response = await client.post('http://127.0.0.1:8000/api/assistant/bot-telegram',
                    headers={'Authorization':'Bearer ' + settings['control_token'], 'X-MCP-Actor':str(sender['id'])},
                    json={'text':prompt, 'request_id':incoming_request.get() or secrets.token_hex(16)})
            except httpx.TransportError: raise DeliveryPending() from None
        if response.status_code != 200:
            if response.status_code >= 500: raise DeliveryPending()
            return 'Telegram API\nПодключение не подтверждено. Проверьте свою сессию и доступность MCP Telegram или начните заново через /telegram.'
        value=response.json(); command_view.set(value)
        return value['text']
    if re.fullmatch(r'1[A-Za-z0-9_=-]{100,4095}',prompt):
        return 'Telegram API\nДля подключения сначала откройте /telegram. Строка сессии не отправлена ИИ.'
    if command in ('/id','/userid'): return public_reply(sender,'/id')
    if command in ('/myprofile','/setup','/skip'): return await profile_reply(settings,sender['id'],prompt)
    if command in ('/menu','/_refresh_menu','/start','/help'):
        if detail.get('admin_dialog'):
            await server_command(settings,sender['id'],'/admin cancel'); command_view.set(None)
        if command=='/help': return 'Доступные команды:\n' + '\n'.join('/'+key+' — '+label for key,label in available_commands())
        return menu_text(prompt,access)
    if command in ('/hello','/thanks'): return 'Здравствуйте! Откройте /menu.' if any(sections.values()) else 'Здравствуйте! Ваш профиль — /myprofile.'
    if command=='/traffic': command='/subscription'; prompt='/subscription'
    permission=COMMAND_GRANTS.get(command.removeprefix('/'))
    if permission and permission not in grants: return 'Это действие недоступно для вашей учётной записи.'
    if command=='/connect':
        command_view.set({'buttons':[[{'text':'Моя подписка','command':'/subscription'},{'text':'Приложения','command':'/apps'}]]})
        return '1. Установите клиент Mihomo.\n2. Скопируйте ссылку своей подписки и добавьте её в клиент по URL.\n3. Обновите подписку, выберите сервер и включите подключение.'
    if command.startswith('/') and command!='/ai':
        if command not in ('/confirm','/cancel','/operation') and permission is None: return 'Откройте /help — доступные команды.'
        return await server_command(settings,sender['id'],prompt)
    if detail.get('admin_dialog') and not command.startswith('/'):
        if len(prompt)>220: return 'Введите короткое значение, до 220 символов.'
        return await server_command(settings,sender['id'],'/admin input '+prompt)
    if not sections.get('ai'): return 'AI-ассистент не подключён. Ваш профиль — /myprofile.'
    if command=='/ai': prompt=prompt.split(maxsplit=1)[1] if len(prompt.split(maxsplit=1))>1 else ''
    if not prompt: return 'Напишите вопрос после /ai.'
    async with typing(settings,sender['id']): return await agent_request(settings,sender['id'],prompt)


def public_reply(sender, command):
    if command == '/_refresh_menu':
        return notice('Обновите меню', 'Это меню устарело. Откройте раздел кнопкой ниже или используйте /start.', '↻')
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
    if subscription: text += '\n/subscription — ваша подписка Mihomo\n/profile — параметры и лимиты\n/devices — устройства\n/traffic — трафик\n/apps — приложения для подключения\n/connect — как подключиться'
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
    if access_details.get() is not None: return await scoped_reply(settings,sender,prompt,access)
    if not access: return public_reply(sender, command)
    if command == '/_refresh_menu': return public_reply(sender, command)
    if command in ('/id', '/userid'): return public_reply(sender, '/id')
    if command == '/menu': return menu_text(prompt, access)
    if access == 'subscription' and command in ('/start', '/help'):
        return ('Команды вашей подписки Mihomo работают без AI:\n'
                '/subscription — ссылка\n/profile — параметры, лимиты и срок\n'
                '/devices — устройства\n/traffic — учтённый трафик\n/apps — приложения для подключения\n/connect — как подключиться\n/id — ваш Telegram ID.\n'
                'Можно писать обычным текстом: «мой трафик», «мои устройства», «моя подписка».')
    if access == 'subscription' and command not in (*SUBSCRIPTION_READS, '/confirm', '/cancel', '/operation'):
        return 'Доступна ваша подписка Mihomo: /subscription. AI-ассистент для этой учётной записи не подключён.'
    if command in ('/start', '/help'):
        return assistant_help(access is True, access in (True, 'chat-subscription'))
    if command == '/hello': return 'Здравствуйте! /help — возможности и команды. Типовые запросы выполняются без AI.'
    if command == '/thanks': return 'Пожалуйста!'
    if command == '/connect':
        return ('Подключение Mihomo:\n/apps — приложения для подключения. Наш Nodera для Windows: https://github.com/aske312/NoderaClient.\n1. Получите свою ссылку: /subscription.\n'
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
    except DeliveryPending:
        raise
    except Exception:
        return 'Сейчас не получилось подготовить ответ. Попробуйте немного позже.'


BUTTON_COMMANDS = {'Моя подписка': '/subscription', 'Сервер': '/status', 'Ресурсы': '/resources', 'Службы': '/services',
                   'Компоненты': '/components', 'Модели AI': '/models',
                   'Текущая модель': '/model', 'AI по умолчанию': '/model default', 'Помощь': '/help',
                   'Мой профиль': '/profile', 'Мои устройства': '/devices', 'Мой трафик': '/traffic',
                   'Меню': '/menu', 'Приложения': '/apps', 'Как подключиться': '/connect', 'Сеть': '/network', 'Безопасность': '/security', 'Логи': '/logs', 'Диагностика': '/diagnostics'}


def menu_text(prompt, access):
    section = prompt.split(maxsplit=1)[1] if len(prompt.split(maxsplit=1)) > 1 else ''
    if access_details.get() is not None:
        sections=access_details.get().get('sections', {})
        if prompt.split()[0] == '/start':
            return 'Nodera\nВаш помощник в Telegram: управление подпиской и общение с ИИ при наличии доступа. Новые возможности появятся позже.\n\nОткройте нужный раздел кнопками ниже.'
        return {'subscription':'Подписка и подключение\nПодписка, устройства и приложения для подключения.',
                'server':'Сервер\nСостояние, службы и диагностика.',
                'ai':'AI-ассистент\nНапишите вопрос или прикрепите файл. Переписка с ИИ сохраняется.',
                'admin':'Админ Mihomo\nВыберите подписку или создайте новую.'}.get(section,'Меню Nodera\nВыберите нужный раздел.') if not section or sections.get(section) else 'Раздел недоступен для вашей учётной записи.'
    if section == 'subscription' and access in (True, 'subscription', 'chat-subscription'):
        return 'Подписка и подключение. Выберите нужное действие ниже.'
    if section == 'server' and access is True:
        return 'Сервер. Состояние и диагностика без обращения к AI.'
    if section == 'ai' and access in (True, 'chat', 'chat-subscription'):
        return 'AI-ассистент. Напишите вопрос, приложите файл или выберите модель. Сообщения обрабатываются по очереди.'
    return 'Меню Nodera. Выберите раздел. Вопросы можно писать обычным сообщением.'


def reply_keyboard(access):
    if not access: return {'remove_keyboard': True}
    return {'keyboard': [[{'text': 'Меню'}]], 'resize_keyboard': True, 'is_persistent': True,
            'input_field_placeholder': 'Напишите сообщение или откройте меню…'}


def inline_keyboard_content(answer, prompt, access, actions):
    """Opaque callbacks are scoped to one actor; long model IDs stay server-side."""
    rows = []
    def button(text, command):
        key = secrets.token_hex(8)
        actions[key] = command
        return {'text': text[:60], 'callback_data': 'nd:' + key}
    command = prompt.split()[0].split('@')[0].lower()
    view=command_view.get()
    if view is not None and 'buttons' in view:
        for row in view['buttons'][:32]:
            buttons=[]
            for item in row[:3]:
                if isinstance(item.get('url'),str) and item['url'].startswith('https://'):
                    buttons.append({'text':str(item['text'])[:60],'url':item['url']})
                elif isinstance(item.get('web_app'), str) and item['web_app'].startswith('https://'):
                    buttons.append({'text': str(item['text'])[:60], 'web_app': {'url': item['web_app']}})
                elif isinstance(item.get('command'),str) and item['command'].startswith('/') and len(item['command'])<=256:
                    buttons.append(button(item['text'],item['command']))
            if buttons: rows.append(buttons)
        if rows: return {'inline_keyboard':rows}
    if access_details.get() is not None and command in ('/menu','/start','/help','/_refresh_menu'):
        detail=access_details.get(); sections=detail.get('sections',{}); grants=set(detail['permissions'])
        section=prompt.split(maxsplit=1)[1] if command=='/menu' and len(prompt.split(maxsplit=1))>1 else ''
        groups={'subscription':[(('Моя подписка','/subscription'),('Статус','/profile')),(('Устройства','/devices'),('Приложения','/apps')),(('Как подключиться','/connect'),)],
                'server':[(('Состояние','/status'),('Службы','/services')),(('Сеть','/network'),('Безопасность','/security')),(('Логи','/logs'),('Диагностика','/diagnostics'))],
                'ai':[(('Выбрать модель','/models'),('Текущая модель','/model'))]}
        if section in groups and sections.get(section):
            for pair in groups[section]:
                row=[button(label,cmd) for label,cmd in pair if COMMAND_GRANTS.get(cmd[1:]) in grants]
                if row: rows.append(row)
        elif section=='admin' and sections.get('admin'): rows.append([button('Подписки','/admin list'),button('Создать','/admin create')] if 'subscriptions_create' in grants else [button('Подписки','/admin list')])
        elif not section:
            for key,label in [('subscription','Подписка Mihomo'),('ai','ИИ-ассистент'),('telegram','Telegram API'),('server','Контроль VPS'),('admin','Админ Mihomo')]:
                if sections.get(key): rows.append([button(label,'/telegram' if key=='telegram' else '/menu '+key)])
            if sections.get('subscription_setup'): rows.append([button('Оформить подписку Mihomo','/subscription')])
            if not any(sections.values()): rows.append([button('Мой профиль','/myprofile')] + ([button('Мой ID','/id')] if detail.get('role','guest')=='guest' else []))
        if section: rows.append([button('← Главное меню','/menu')])
        return {'inline_keyboard':rows} if rows else None
    if '/skip' in answer and profile_state.get() in ('new', 'name', 'birthdate'):
        return {'inline_keyboard': [[button('Пропустить', '/skip')]]}
    if command == '/myprofile':
        return {'inline_keyboard': [[button('Изменить профиль', '/setup')]]}
    if access and command in ('/menu', '/start', '/help', '/_refresh_menu'):
        section = prompt.split(maxsplit=1)[1] if command == '/menu' and len(prompt.split(maxsplit=1)) > 1 else ''
        if section == 'subscription' and access in (True, 'subscription', 'chat-subscription'):
            for labels in [(('Ссылка подписки','/subscription'),('Профиль','/profile')), (('Устройства','/devices'),('Трафик','/traffic')), (('Приложения','/apps'),('Как подключиться','/connect'))]:
                rows.append([button(label, action) for label, action in labels])
        elif section == 'server' and access is True:
            for labels in [(('Состояние','/status'),('Ресурсы','/resources')), (('Службы','/services'),('Компоненты','/components')), (('Сеть','/network'),('Безопасность','/security')), (('Логи','/logs'),('Диагностика','/diagnostics'))]:
                rows.append([button(label, action) for label, action in labels])
        elif section == 'ai' and access in (True, 'chat', 'chat-subscription'):
            rows.append([button('Выбрать модель','/models'),button('Текущая модель','/model')])
        else:
            if access in (True, 'subscription', 'chat-subscription'): rows.append([button('Подписка и подключение','/menu subscription')])
            if access in (True, 'chat', 'chat-subscription'): rows.append([button('AI-ассистент','/menu ai')])
            if access is True: rows.append([button('Сервер','/menu server')])
            rows.append([button('Мой ID','/id')])
        if section: rows.append([button('← Главное меню','/menu')])
    if access and command == '/apps' and len(prompt.split()) == 1:
        rows += [[button(label, '/apps ' + key)] for key,label in [('windows','Windows · Nodera'),('android','Android'),('ios','iPhone / iPad'),('macos','macOS'),('linux','Linux')]]
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


def inline_keyboard(answer, prompt, access, actions):
    markup = inline_keyboard_content(answer, prompt, access, actions)
    detail = access_details.get()
    command = prompt.split()[0].split('@')[0].lower()
    if detail is None or not command.startswith('/') or command == '/ai': return markup
    if profile_state.get() in ('new', 'name', 'birthdate') and (command_view.get() or {}).get('buttons'): return markup
    rows = (markup or {}).get('inline_keyboard', [])
    targets = set(actions.values())
    section = ('subscription' if command in ('/subscription','/profile','/devices','/apps','/connect') else
               'server' if command in ('/status','/resources','/services','/components','/network','/security','/logs','/logfile','/diagnostics') else
               'admin' if command in ('/admin','/confirm','/cancel','/operation') else
               'ai' if command in ('/model','/models') else None)
    navigation = []
    def add(label, target):
        if target in targets: return
        key = secrets.token_hex(8); actions[key] = target
        navigation.append({'text':label, 'callback_data':'nd:' + key})
    if command == '/apps' and len(prompt.split()) > 1: add('← Платформы','/apps')
    elif command == '/logfile': add('← Журналы','/logs')
    elif section and detail.get('sections',{}).get(section) and command != '/menu': add('← Назад','/menu ' + section)
    if command not in ('/menu','/start','/help','/_refresh_menu'):
        add('Главное меню','/menu')
    if navigation: rows.append(navigation)
    return {'inline_keyboard':rows} if rows else None


def bot_message(text):
    """One compact heading style for bot screens; model replies bypass this."""
    lines = text.strip().splitlines()
    if not lines: return text
    heading = re.sub(r'^#{1,6}\s*', '', lines[0].strip()).replace('**','')
    if len(heading) > 100: return text
    body = '\n'.join(lines[1:]).strip()
    return '**' + heading + '**' + ('\n\n' + body if body else '')


def bot_request_id(token, actor, key):
    return hashlib.sha256(f'{token}:{actor}:{key}'.encode()).hexdigest()[:32]


def bot_state_directory(base, token):
    bot_id = token.split(':', 1)[0]
    identity = hashlib.sha256(bot_id.encode()).hexdigest()[:24]
    directory = base / 'bots' / identity
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    imported = directory / '.legacy-inbox-imported'
    if imported.exists():
        return directory
    # The previous offset and menu message IDs have no owner marker. Recover
    # only inbox entries whose request IDs identify the current token.
    inbox = DurableInbox(directory)
    for path in (base / 'inbox').glob('*.json'):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            actor = row['sender']['id']
            if row['request_id'] == bot_request_id(token, actor, path.stem) and inbox.put(path.stem, row):
                path.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            continue
    imported.touch(mode=0o600)
    return directory


async def bot(settings):
    if not settings['modules']['nodera-bot'] or not settings.get('bot_token'):
        raise ValueError('Нужен токен включённого NoderaBot')
    if (await bot_api(settings, 'getWebhookInfo')).get('url'):
        raise ValueError('У бота установлен webhook; long polling не запущен')
    try: await command_menu(settings)
    except Exception: logging.error('NoderaBot command menu could not be updated')
    base_state = Path(os.environ.get('STATE_DIRECTORY', tempfile.gettempdir()))
    state_root = bot_state_directory(base_state, settings['bot_token'])
    state_file = state_root / 'offset.json'
    offset = json.loads(state_file.read_text()).get('offset', 0) if state_file.exists() else 0
    menus = {}
    callbacks = {}
    media_groups = {}
    dispatcher = MessageDispatcher()
    inbox = DurableInbox(state_root)
    inbox.acknowledge(offset)
    notices = {}
    ui_file = state_root / 'dialog-messages.json'
    ui_messages = json.loads(ui_file.read_text(encoding='utf-8')) if ui_file.exists() else {}
    def remember_ui(actor, identity):
        ui_messages.pop(str(actor),None)
        if identity: ui_messages[str(actor)] = identity
        while len(ui_messages)>1000: ui_messages.pop(next(iter(ui_messages)))
        temporary=ui_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(ui_messages),encoding='utf-8'); temporary.chmod(0o600); temporary.replace(ui_file)
    def remember_notice(actor, sent):
        if not isinstance(sent,dict) or type(sent.get('message_id')) is not int: return
        previous = ui_messages.get(str(actor), [])
        identities = list(previous) if isinstance(previous,list) else [previous]
        remember_ui(actor, identities + [sent['message_id']])
    async def deliver(sender, prompt, messages=None, alert=None, context=None):
        actor = sender['id']
        try: access = await identify(settings, sender)
        except ContactBlocked: return
        except Exception: raise DeliveryPending() from None
        if not prompt and not messages: return
        admin = access is True
        scope = access_signature(access)
        role = scope
        if menus.get(actor) != role:
            try:
                await command_menu(settings, actor, admin, access in ('chat', 'chat-subscription'), access in (True, 'subscription', 'chat-subscription'))
                if len(menus) >= 1000: menus.clear()
                menus[actor] = role
            except Exception: logging.error('NoderaBot private command menu could not be updated')
        cached = delivery_record.get()
        cached_row = inbox.read(cached) if cached else {}
        if (cached_row.get('telegram_input') and access_details.get() is not None and
                'telegram_read' in access_details.get().get('permissions', [])):
            access_details.set({**access_details.get(), 'telegram_dialog':True})
        telegram_input = (access_details.get() or {}).get('telegram_dialog') and not prompt.startswith('/') and not messages and not alert
        session_message = not messages and not alert and re.fullmatch(r'1[A-Za-z0-9_=-]{100,4095}', prompt)
        if context and not telegram_input and not session_message:
            await remember_incoming(settings, context)
        if telegram_input:
            if cached: inbox.amend(cached, telegram_input=True)
        command = prompt.split()[0].split('@')[0].lower() if prompt else ''
        deterministic = cached_row.get('bot_interaction', not messages and not alert and command != '/ai' and (
            command.startswith('/') or profile_state.get() in ('name','birthdate') or
            (access_details.get() or {}).get('admin_dialog') or (access_details.get() or {}).get('telegram_dialog') or
            re.fullmatch(r'1[A-Za-z0-9_=-]{100,4095}',prompt or '') or not access))
        # Persist routing before profile/admin state advances, including lost replies.
        if cached: inbox.amend(cached, bot_interaction=bool(deterministic))
        cleanup = list(cached_row.get('cleanup', []))
        if not cleanup:
            previous = ui_messages.get(str(actor), [])
            cleanup = list(previous) if isinstance(previous,list) else [previous]
            if deterministic and context and context.get('chat',{}).get('id')==actor and context.get('from',{}).get('id')==actor:
                cleanup.append(context.get('message_id'))
            cleanup = list(dict.fromkeys(identity for identity in cleanup if type(identity) is int and identity>0))
            if cached: inbox.amend(cached, cleanup=cleanup)
        session_removed = cached_row.get('session_message_removed', False)
        if session_message and not session_removed and 'answer' not in cached_row:
            if context and context.get('chat', {}).get('id') == actor and context.get('from', {}).get('id') == actor:
                try:
                    await bot_api(settings, 'deleteMessage', chat_id=actor, message_id=context['message_id'])
                except Exception:
                    pass
                else:
                    session_removed = True
                    if cached: inbox.amend(cached, session_message_removed=True)
        if 'answer' in cached_row:
            same_scope = cached_row.get('answer_scope',scope if access_details.get() is None else None)==scope
            answer = cached_row['answer'] if cached_row.get('answer_access') == access and same_scope else 'Доступ изменился. Откройте /menu для доступных действий.'
            command_view.set(cached_row.get('view') if same_scope else None)
        elif session_message and not session_removed:
            answer = ('Telegram API\nНе удалось удалить сообщение со строкой сессии. Эта строка не была обработана. '
                      'Удалите сообщение вручную и завершите соответствующий сеанс в Telegram → Настройки → Устройства. '
                      'Затем создайте новую StringSession и начните подключение заново.')
            command_view.set({'buttons': [[{'text': 'Начать заново', 'command': '/telegram session'}]]})
        elif session_message and not telegram_input:
            answer = 'Telegram API\nСообщение удалено. Сначала откройте настройку подключения, затем отправьте строку заново.'
            command_view.set({'buttons': [[{'text': 'Настроить подключение', 'command': '/telegram session'}]]})
        elif command != '/telegram' and not messages and not alert and not (access_details.get() or {}).get('admin_dialog') and not (access_details.get() or {}).get('telegram_dialog') and (cached_row.get('profile_dialog') or profile_state.get() is not None and (
                profile_state.get() in ('name', 'birthdate') or prompt.split(maxsplit=1)[0].split('@')[0].lower() in ('/start', '/myprofile', '/setup', '/skip', '/skipall'))):
            # Remember routing before sending: a lost final-profile reply must
            # reconcile the same receipt even when the questionnaire is now done.
            if cached: inbox.amend(cached, profile_dialog=True)
            answer = await profile_reply(settings, actor, prompt)
            if answer is None: answer = await reply(settings, sender, prompt, access)
        elif alert:
            if access is not True or access_details.get() is not None and 'chat' not in access_details.get().get('permissions',[]):
                answer = 'Разбор алерта доступен владельцу с доступом к ИИ.'
            else:
                try:
                    async with typing(settings, actor):
                        answer = await agent_request(settings, actor, '', alert=alert)
                except DeliveryPending:
                    raise
                except Exception:
                    answer = 'Не удалось запустить разбор алерта. Проверьте доступность AI-ассистента и повторите позже.'
        else:
            answer = await attachment_reply(settings, sender, prompt, messages, access) if messages else await reply(settings, sender, prompt, access)
        if access or access_details.get() is not None:
            # A request can outlive a permission change.
            try: current_access = await identify(settings, sender)
            except ContactBlocked: return
            except Exception: raise DeliveryPending() from None
            if current_access != access or access_signature(current_access)!=scope:
                access = False
                answer = 'Доступ изменился. Откройте /menu для доступных действий.'
                command_view.set(None)
        if not answer: return
        if cached: inbox.amend(cached, answer=answer, answer_access=access, answer_scope=access_signature(access), view=command_view.get())
        if len(callbacks) >= 1000: callbacks.clear()
        actions = {}
        markup = inline_keyboard(answer, prompt or 'Вложения', access, actions)
        if actions:
            callbacks.setdefault(actor, {}).update(actions)
            while len(callbacks[actor]) > 256: callbacks[actor].pop(next(iter(callbacks[actor])))
        if deterministic: answer = bot_message(answer)
        document = (command_view.get() or {}).get('document')
        sent_id = cached_row.get('sent_message_id')
        sent_ids = list(cached_row.get('sent_message_ids', [sent_id] if sent_id else []))
        for index,chunk in enumerate(telegram_chunks(answer)):
            if index < cached_row.get('sent', 0): continue
            if document and index==0:
                sent = await bot_api(settings,'sendDocument',chat_id=actor,document=document,caption=chunk[:1000],parse_mode='HTML',reply_markup=markup or reply_keyboard(access))
            else:
                keyboard = markup if index == 0 and markup else reply_keyboard(access)
                try:
                    sent = await bot_api(settings, 'sendMessage', chat_id=actor, text=chunk, parse_mode='HTML', reply_markup=keyboard)
                except TelegramAPIError as error:
                    if error.status != 400 or index != 0:
                        raise
                    logging.warning('NoderaBot formatted reply rejected: %s', error)
                    web_app = any('web_app' in item for row in (markup or {}).get('inline_keyboard', []) for item in row)
                    text = ('Telegram отклонил кнопку открытия формы. Откройте настройки Telegram MCP в панели Nodera.'
                            if web_app else 'Telegram отклонил оформление ответа. Если команда меняла настройки, проверьте её результат в панели.')
                    # A confirmed HTTP 400 means the original message was not accepted.
                    sent = await bot_api(settings, 'sendMessage', chat_id=actor, text=text)
                    if isinstance(sent, dict) and type(sent.get('message_id')) is int:
                        sent_ids.append(sent['message_id'])
                    if cached: inbox.amend(cached, sent=len(telegram_chunks(answer)), sent_message_ids=sent_ids)
                    break
            if isinstance(sent,dict) and type(sent.get('message_id')) is int:
                sent_id=sent['message_id']; sent_ids.append(sent_id)
            if cached: inbox.amend(cached, sent=index + 1, sent_message_id=sent_id, sent_message_ids=sent_ids)
        previous = ui_messages.get(str(actor), [])
        cleanup = list(dict.fromkeys(cleanup + (previous if isinstance(previous,list) else [previous])))
        if cached: inbox.amend(cached,cleanup=cleanup)
        remember_ui(actor,sent_ids if deterministic else None)
        for identity in cleanup:
            if identity in sent_ids: continue
            if session_message and context and identity == context.get('message_id'): continue
            try: await bot_api(settings,'deleteMessage',chat_id=actor,message_id=identity)
            except Exception:
                try: await bot_api(settings,'editMessageReplyMarkup',chat_id=actor,message_id=identity,reply_markup={'inline_keyboard':[]})
                except Exception: pass
        if (command_view.get() or {}).get('decision_complete'):
            identity = cached_row.get('replace_message_id')
            # Confirmations originating in an AI answer disable its buttons,
            # while the answer itself remains part of the conversation.
            if type(identity) is int and identity>0 and identity not in cleanup:
                try: await bot_api(settings,'editMessageReplyMarkup',chat_id=actor,message_id=identity,reply_markup={'inline_keyboard':[]})
                except Exception: pass
    async def deliver_saved(row, key):
        identity = incoming_request.set(row['request_id'])
        cached = delivery_record.set(key)
        detail_token=access_details.set(None); view_token=command_view.set(None)
        sink_token=transient_sink.set(remember_notice)
        try:
            if row.get('edited'):
                await remember_incoming(settings, row['context']); return
            messages = row.get('messages')
            prompt = row['prompt']
            if row.get('album'):
                media_groups.pop(row['album'], None)
                prompt = '\n'.join(message.get('caption', '').strip() for message in messages if message.get('caption'))
                for message in messages: await remember_incoming(settings, message)
            await deliver(row['sender'], prompt, messages, row.get('alert'), context=row.get('context'))
        finally:
            incoming_request.reset(identity); delivery_record.reset(cached); access_details.reset(detail_token); command_view.reset(view_token); transient_sink.reset(sink_token)
    async def flush_inbox():
        while True:
            try: inbox.pump(dispatcher, deliver_saved)
            except (OSError, ValueError): logging.error('NoderaBot inbox could not be read')
            await asyncio.sleep(.5)
    pump_task = asyncio.create_task(flush_inbox())
    def accept(update, sender, prompt, messages=None, alert=None, context=None, command=False, edited=False, album=None, replace_message_id=None):
        actor = sender['id']
        key = 'album-' + hashlib.sha256(f'{actor}:{album}'.encode()).hexdigest()[:32] if album else 'message-' + str(update['update_id'])
        record = {'order':update['update_id'], 'sender':sender, 'prompt':prompt, 'messages':messages,
                  'context':context, 'alert':alert, 'command':command, 'edited':edited, 'replace_message_id':replace_message_id,
                  'request_id':bot_request_id(settings['bot_token'], actor, key)}
        if album: record.update(album=album, not_before=time.time() + 1)
        fresh = not (inbox.root / (key + '.json')).exists()
        accepted = inbox.put(key, record)
        if accepted: inbox.pump(dispatcher, deliver_saved)
        return accepted, key, fresh
    try:
        while True:
            try:
                updates = await bot_api(settings, 'getUpdates', offset=offset, timeout=25, allowed_updates=['message', 'edited_message', 'callback_query'])
                for update in updates:
                    callback = update.get('callback_query')
                    message = callback.get('message', {}) if callback else update.get('message', update.get('edited_message', {}))
                    sender = callback.get('from', {}) if callback else message.get('from', {})
                    actor = sender.get('id')
                    if message.get('chat', {}).get('type') != 'private' or type(actor) is not int or actor <= 0 or sender.get('is_bot'): continue
                    if update.get('edited_message'):
                        accept(update, sender, '', context=message, command=True, edited=True)
                        continue  # Edits update context without executing commands again.
                    messages = None
                    alert = None
                    if callback:
                        expired = False
                        try:
                            await bot_api(settings, 'answerCallbackQuery', callback_query_id=callback['id'])
                        except TelegramAPIError as error:
                            expired = error.status in (400, 403)
                            logging.warning('NoderaBot callback acknowledgement rejected: %s', error)
                        except httpx.TransportError:
                            logging.warning('NoderaBot callback acknowledgement connection failed')
                        key = callback.get('data', '')
                        match = re.fullmatch(r'alert:([0-9a-f]{32}):(brief|deep)', key)
                        if expired:
                            prompt = '/_refresh_menu'
                        elif match and message.get('chat', {}).get('id') == actor:
                            alert = {'token': match[1], 'mode': match[2]}
                            prompt = 'Разбор алерта с ИИ'
                        else:
                            prompt = callbacks.get(actor, {}).get(key[3:], '') if key.startswith('nd:') else ''
                        if not prompt:
                            # Refresh through the normal delivery path so an old
                            # callback cannot bypass current guest blocking.
                            prompt = '/_refresh_menu'
                    else:
                        messages = [message] if message.get('photo') or message.get('document') else None
                        prompt = (message.get('caption', '') if messages else message.get('text', '')).strip()
                        if not messages: prompt = direct_prompt(BUTTON_COMMANDS.get(prompt, prompt))
                    album_key = (actor, str(message['media_group_id'])) if messages and message.get('media_group_id') else None
                    if album_key in media_groups:
                        # Retain one overflow entry to reject oversized albums;
                        # never grow memory with an unbounded group of updates.
                        if len(media_groups[album_key][0]) < 5 and (not message.get('message_id') or all(old.get('message_id') != message['message_id'] for old in media_groups[album_key][0])):
                            media_groups[album_key][0].append(message)
                            inbox.amend(media_groups[album_key][1], messages=media_groups[album_key][0])
                        continue
                    is_command = not messages and prompt.startswith('/') and prompt.split()[0].split('@')[0].lower() not in ('/ai', '/start', '/setup', '/myprofile', '/skip', '/skipall', '/menu', '/admin', '/telegram')
                    # Explicit commands remain responsive while this user's AI
                    # dialog waits; commands themselves keep their own ordering.
                    stream = (actor, 'command') if is_command else actor
                    accepted, inbox_key, fresh = accept(update, sender, prompt, messages, alert,
                        context=None if callback or album_key else message, command=is_command,
                        album=str(message['media_group_id']) if album_key else None,
                        replace_message_id=message.get('message_id') if callback and not alert and str(message.get('from',{}).get('id'))==settings['bot_token'].split(':')[0] else None)
                    if album_key and accepted: media_groups[album_key] = (messages, inbox_key)
                    if fresh and (not accepted or inbox.waiting(actor, is_command) > 1) and time.monotonic() - notices.get(actor, 0) >= 10:
                        notices[actor] = time.monotonic()
                        if len(notices) > 1000: notices = {actor: time.monotonic()}
                        try:
                            await identify(settings, sender)
                            sent = await bot_api(settings, 'sendMessage', chat_id=actor, text=telegram_html(notice('Сообщение в очереди' if accepted else 'Очередь заполнена', QUEUE_NOTICE if accepted else BUSY_NOTICE, '⏳')), parse_mode='HTML')
                            remember_notice(actor,sent)
                        except ContactBlocked:
                            continue
                        except (TelegramAPIError, httpx.TransportError, DeliveryPending):
                            logging.warning('NoderaBot queue notice could not be delivered')
                if updates:
                    offset = max(offset, max(update['update_id'] for update in updates) + 1)
                    temporary = state_file.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'offset': offset})); temporary.chmod(0o600); temporary.replace(state_file)
                    inbox.acknowledge(offset)
            except TelegramAPIError as error:
                logging.error('NoderaBot polling or delivery failed: %s', error)
                await asyncio.sleep(5)
            except Exception:
                logging.error('NoderaBot polling or delivery failed')
                await asyncio.sleep(5)
    finally:
        pump_task.cancel()
        with suppress(asyncio.CancelledError): await pump_task
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
