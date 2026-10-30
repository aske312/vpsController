"""Import instructions for Mihomo YAML subscriptions."""
from html import escape


CLIENT_HINTS = {"happ": "Happ", "singbox": "sing-box", "v2rage": "v2RAGE"}


def import_page(url, verified_clients=()):
    apps = "".join(f'<a href="{escape(record["app_url"], quote=True)}" rel="noreferrer">{escape(record["client_name"])} {escape(record["client_version"])} · {escape(record["os"])} {escape(record["os_version"])} · {escape(record["core_name"])} {escape(record["core_version"])}</a>' for record in verified_clients)
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение профиля</title><style>body{font:17px system-ui;background:#0c1520;color:#eef3fa;max-width:520px;margin:8vh auto;padding:24px}a{display:block;background:#473c86;color:white;padding:18px;margin:16px 0;border-radius:12px;text-decoration:none}input{box-sizing:border-box;width:100%;padding:14px;font:14px system-ui}p{line-height:1.6}</style>
<h1>Подключить профиль</h1>''' + \
        ('<p>Приложения, проверенные для этих подключений:</p>' + apps if apps else '<p>Для этих подключений пока нет проверенных приложений.</p>') + \
        '<p>Добавьте общую ссылку как подписку в клиент с поддержкой Mihomo/Clash YAML. Экспорт sing-box/Xray JSON и URI-подписок больше не поддерживается. После изменения настроек обновите подписку.</p>' + \
        f'<input readonly aria-label="Общая подписка" value="{escape(url, quote=True)}"></html>'
