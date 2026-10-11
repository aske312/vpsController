"""Apply settings to an existing owned WG/AWG config without installation."""
import ipaddress
import re

from profile_recovery import capture, decode, restore_file

AWG_FIELDS = {"jc": "Jc", "jmin": "Jmin", "jmax": "Jmax", "s1": "S1", "s2": "S2",
              "h1": "H1", "h2": "H2", "h3": "H3", "h4": "H4"}


def render(text, module, previous, values):
    sections = list(re.finditer(r"(?m)^\s*\[([^]\r\n]+)\][ \t]*\r?$", text))
    interfaces = [match for match in sections if match.group(1) == "Interface"]
    if len(interfaces) != 1:
        raise RuntimeError("Не найдена однозначная секция Interface WG/AWG")
    breaking = {"port", "subnet"} | ({"s1", "s2", "h1", "h2", "h3", "h4"} if module == "transport-awg" else set())
    if any(match.group(1) == "Peer" for match in sections) and any(previous.get(key) != values.get(key) for key in breaking):
        raise RuntimeError("Конфигурация содержит peers; ломающие изменения требуют перехода на новые подключения")
    start = interfaces[0].end()
    end = next((match.start() for match in sections if match.start() > start), len(text))
    body = text[start:end]
    def field(key, replacement=None):
        nonlocal body
        pattern = re.compile(r"(?m)^([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)([^\r\n]*)(\r?)$")
        matches = list(pattern.finditer(body))
        if len(matches) != 1:
            raise RuntimeError(f"Неоднозначное или отсутствующее поле {key} в WG/AWG")
        original = matches[0].group(2).strip()
        if replacement is not None:
            body = pattern.sub(lambda match: match.group(1) + str(replacement) + match.group(3), body)
        return original
    if not field("PrivateKey"):
        raise RuntimeError("В конфигурации WG/AWG отсутствует ключ интерфейса")
    old_port = int(field("ListenPort"))
    if old_port != int(previous["port"]):
        raise RuntimeError("Сохранённый порт WG/AWG расходится с runtime-конфигурацией")
    new_port = int(values["port"])
    field("ListenPort", new_port)
    field("MTU", int(values["mtu"]))
    old_net = ipaddress.ip_network(previous["subnet"], strict=True)
    new_net = ipaddress.ip_network(values["subnet"], strict=True)
    if old_net != new_net:
        address = ipaddress.ip_interface(field("Address"))
        if address.network != old_net:
            raise RuntimeError("Сохранённая подсеть WG/AWG расходится с runtime-конфигурацией")
        field("Address", f"{next(new_net.hosts())}/{new_net.prefixlen}")
    if old_port != new_port or old_net != new_net:
        for key in ("PostUp", "PostDown"):
            hook = field(key)
            if old_port != new_port:
                hook, count = re.subn(r"(--dport\s+)" + str(old_port) + r"(?=\s|;|$)", lambda match: match.group(1) + str(new_port), hook)
                if not count:
                    raise RuntimeError("Нестандартное правило порта WG/AWG; автоматическое изменение запрещено")
            if old_net != new_net:
                hook, count = re.subn(r"(-s\s+)" + re.escape(str(old_net)) + r"(?=\s|;|$)", lambda match: match.group(1) + str(new_net), hook)
                if not count:
                    raise RuntimeError("Нестандартное правило подсети WG/AWG; автоматическое изменение запрещено")
            field(key, hook)
    if module == "transport-awg":
        for name, key in AWG_FIELDS.items():
            field(key, int(values[name]))
    return text[:start] + body + text[end:]


def prepare(api, module, previous, values):
    path = api.WG_CONFIG_BY_MODULE[module]
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Управляемая конфигурация WG/AWG отсутствует или заменена ссылкой")
    original = path.read_bytes()
    return original, render(original.decode("utf-8"), module, previous, values).encode("utf-8")


def apply(api, module, candidate):
    path = api.WG_CONFIG_BY_MODULE[module]
    original, content = candidate
    if path.is_symlink() or path.read_bytes() != original:
        raise RuntimeError("Конфигурация WG/AWG изменилась перед применением настроек")
    saved = decode(capture(path))
    unit = api.SERVICE_BY_MODULE[module]
    # wg-quick reads PostDown from the file: stop with the OLD file in place.
    api.run("systemctl", "stop", unit, check=True)
    if api.observe_service(unit)["runtime"]["state"] != "stopped":
        raise RuntimeError("Остановка WG/AWG не подтверждена")
    restore_file(path, {**saved, "content": content})
    api.run("systemctl", "start", unit, check=True)
    if not api.service_stably_active(unit):
        raise RuntimeError("Служба WG/AWG не осталась активной после изменения настроек")
