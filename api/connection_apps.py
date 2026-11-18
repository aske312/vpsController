"""Public connection-app catalog shared with the panel; no subscription credentials."""
import json
from pathlib import Path

from fastapi import HTTPException

PLATFORMS = {'windows': 'Windows', 'android': 'Android', 'ios': 'iPhone / iPad', 'macos': 'macOS', 'linux': 'Linux'}


def guide(platform=None):
    if platform is None:
        return ('Приложения для подключения\nВыберите вашу платформу:\n' +
                '\n'.join('/apps ' + key + ' — ' + name for key, name in PLATFORMS.items()) +
                '\n\nNodera — наш клиент для Windows 10/11 x64: https://github.com/aske312/NoderaClient\n'
                'После установки добавьте ссылку подписки через «+». /subscription — получить ссылку; /connect — инструкция.')
    if platform not in PLATFORMS:
        raise HTTPException(422, 'Платформа: windows, android, ios, macos или linux. Пример: /apps windows')
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'protocol-images/mihomo/client-apps.json').read_text(encoding='utf-8'))
    rows = [row for row in catalog if platform in row['platforms']]
    return ('Приложения для ' + PLATFORMS[platform] + '\n\n' +
            '\n\n'.join(row['name'] + ' · ' + row['label'] + '\n' + row['url'] for row in rows) +
            '\n\nУстановите приложение и импортируйте HTTPS-ссылку подписки по URL. /subscription — ваша ссылка; /connect — инструкция.')
