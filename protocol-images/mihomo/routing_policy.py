"""Structured rules/groups stored in the existing scalar routing contract."""
import ipaddress
import json
import re
import urllib.parse
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

GROUP = 'GATE.312'
KINDS = ('domain', 'domain-suffix', 'ip-cidr', 'geoip', 'geosite', 'process')
LIBRARY_PRESETS = {'block_malware', 'block_advertising', 'block_privacy', 'quic_compatibility',
                   'direct_ru_services', 'direct_ru_sites', 'direct_ru_banks', 'direct_ru_marketplaces',
                   'direct_downloads', 'windows_geolocation', 'direct_local_network', 'direct_games_enabled',
                   'direct_games_udp_enabled', 'direct_p2p_enabled', 'udp_tunnel_exclusions'}
GEO_CATALOG = {
    **{'geoip-' + name: {'behavior': 'ipcidr', 'format': 'text', 'interval': 86400,
        'url': f'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/{name}.list'} for name in ('cn', 'ru')},
    **{'geosite-' + name: {'behavior': 'domain', 'format': 'text', 'interval': 86400,
        'url': f'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/{name}.list'} for name in ('cn', 'youtube', 'telegram', 'github')},
}


class Rule(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['domain', 'domain-suffix', 'ip-cidr', 'geoip', 'geosite', 'process', 'library']
    value: str = Field(min_length=1, max_length=253)
    target: str = Field(default=GROUP, min_length=1, max_length=48)


class Group(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')
    type: Literal['select', 'fallback', 'url-test', 'load-balance'] = 'fallback'
    members: list[str] = Field(default_factory=lambda: ['*'], min_length=1, max_length=100)
    strategy: Literal['consistent-hashing', 'round-robin', 'sticky-sessions'] = 'consistent-hashing'
    url: str | None = Field(default=None, max_length=1024)
    interval: int | None = Field(default=None, strict=True, ge=10, le=3600)
    timeout: int | None = Field(default=None, strict=True, ge=1000, le=10000)
    max_failed_times: int | None = Field(default=None, strict=True, ge=1, le=10)
    tolerance: int | None = Field(default=None, strict=True, ge=0, le=1000)

    @field_validator('url')
    @classmethod
    def health_url(cls, value):
        if value is None: return value
        parsed = urllib.parse.urlsplit(value)
        if re.search(r'[\x00-\x20]', value) or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Укажите HTTP(S) URL проверки без пароля и фрагмента')
        parsed.port  # Validate a malformed or out-of-range port too.
        return value


class Policy(BaseModel):
    model_config = {'extra': 'forbid'}
    groups: list[Group] = Field(default_factory=list, max_length=20)
    rules: list[Rule] = Field(default_factory=list, max_length=200)


def parse(raw):
    if raw == '@inherit': return Policy()
    try:
        if not isinstance(raw, str) or len(raw) > 100000: raise ValueError()
        policy = Policy.model_validate(json.loads(raw or '{}'))
        names = [group.name for group in policy.groups]
        if len(set(names)) != len(names) or any(name in ('DIRECT', 'REJECT', 'GLOBAL') for name in names): raise ValueError('Повторяющееся или зарезервированное имя группы')
        identities = [group.id or group.name for group in policy.groups]
        if len(set(identities)) != len(identities): raise ValueError('Повторяющийся ID группы')
        graph = {}
        for group in policy.groups:
            graph[group.name] = []
            if len(group.members) != len(set(group.members)): raise ValueError('Повторяющийся участник группы')
            for member in group.members:
                if member.startswith('group:') and member[6:] in names: graph[group.name].append(member[6:])
                elif member == '*' or re.fullmatch(r'connection:[A-Za-z0-9_-]{1,80}', member): pass
                else: raise ValueError('Группа ссылается на неизвестного участника')
        complete = set()
        def visit(name, path):
            if name in path: raise ValueError('Циклическая ссылка групп')
            if name in complete: return
            for other in graph[name]: visit(other, {*path, name})
            complete.add(name)
        for name in graph: visit(name, set())
        references = set()
        for rule in policy.rules:
            if rule.target not in ('DIRECT', 'REJECT', GROUP, *names): raise ValueError('Правило ссылается на неизвестную группу')
            if rule.kind == 'library':
                if not (rule.value.startswith('preset:') and rule.value[7:] in LIBRARY_PRESETS
                        or re.fullmatch(r'personal:personal-[a-f0-9]{12}', rule.value)):
                    raise ValueError('Неизвестный набор библиотеки правил')
                if rule.value in references: raise ValueError('Для набора уже назначен маршрут')
                references.add(rule.value)
            elif rule.kind in ('domain', 'domain-suffix'):
                if not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', rule.value) or '..' in rule.value:
                    raise ValueError('Укажите домен без URL')
            elif rule.kind == 'ip-cidr': rule.value = str(ipaddress.ip_network(rule.value, strict=False))
            elif rule.kind in ('geoip', 'geosite'):
                if rule.kind + '-' + rule.value not in GEO_CATALOG: raise ValueError('Набор отсутствует в управляемом Geo-каталоге')
            elif not re.fullmatch(r'[a-zA-Z0-9_.@+ -]{1,160}', rule.value): raise ValueError('Недопустимое имя процесса')
        return policy
    except (ValueError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            fields = []
            known = {'groups', 'rules', 'id', 'name', 'type', 'members', 'strategy', 'url', 'interval', 'timeout', 'max_failed_times', 'tolerance', 'kind', 'value', 'target'}
            for error in exc.errors()[:8]:
                fields.append('.'.join(str(part) if isinstance(part, int) or part in known else '?' for part in error['loc']))
            raise HTTPException(422, {'code': 'invalid_routing_policy', 'message': 'Проверьте поля правил и групп: ' + ', '.join(fields), 'fields': fields}) from None
        raise HTTPException(422, 'Некорректная политика маршрутизации: ' + (str(exc)[:180] if not isinstance(exc, ValidationError) else 'проверьте поля правил и групп')) from None


def canonical(raw): return parse(raw).model_dump_json(exclude_none=True)
def group_name(name): return 'policy:' + name


SELECTION_KEY = 'structured_policy_selection'


def selection(raw):
    try:
        if not isinstance(raw, str) or len(raw) > 16000: raise ValueError()
        value = json.loads(raw or '{}')
        if not isinstance(value, dict) or len(value) > 201 or any(
                not isinstance(key, str) or not (key == '@rules' or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key))
                or type(enabled) is not bool for key, enabled in value.items()): raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, 'Некорректный выбор групп маршрутизации') from None


def merge_selection(*values):
    result = {}
    for value in values: result.update(selection('{}' if value is None else value))
    return json.dumps(result, separators=(',', ':'))


def merge_routing(*values):
    result = {}
    for value in values: result.update(value)
    if any(SELECTION_KEY in value for value in values):
        result[SELECTION_KEY] = merge_selection(*(value.get(SELECTION_KEY) for value in values))
    return result


def effective(routing):
    """Selection changes rules, never the shared definitions or their order.

    A disabled group's own rules are omitted. It may still be needed as a
    channel dependency of another enabled group, so retain that dependency.
    """
    policy = parse(routing.get('structured_policy', '{}'))
    choices = selection(routing.get(SELECTION_KEY, '{}'))
    enabled = {group.name for group in policy.groups if choices.get(group.id or group.name, True)}
    rules = [rule for rule in policy.rules if rule.target in enabled or
             rule.target in ('DIRECT', 'REJECT', GROUP) and choices.get('@rules', True)]
    required = set(enabled)
    definitions = {group.name: group for group in policy.groups}
    pending = list(required)
    while pending:
        for member in definitions[pending.pop()].members:
            if member.startswith('group:') and member[6:] not in required:
                required.add(member[6:]); pending.append(member[6:])
    return Policy(rules=rules, groups=[group for group in policy.groups if group.name in required]).model_dump_json(exclude_none=True)


def rules(raw, *, mode=None, os=None):
    result = []
    for rule in parse(raw).rules:
        if rule.kind == 'library': continue  # Resolved by the existing rule generator, in its original position.
        target = rule.target if rule.target in ('DIRECT', 'REJECT', GROUP) else group_name(rule.target)
        if rule.kind == 'process':
            if mode == 'proxy' or os is not None and os not in ('windows', 'linux', 'macos'):
                raise HTTPException(422, 'Правила процессов требуют TUN и поддерживаемую настольную ОС')
            kind = 'PROCESS-NAME'
        elif rule.kind == 'ip-cidr': kind = 'IP-CIDR6' if ':' in rule.value else 'IP-CIDR'
        elif rule.kind in ('geoip', 'geosite'):
            result.append(f'RULE-SET,{rule.kind}-{rule.value},{target}' + (',no-resolve' if rule.kind == 'geoip' else ''))
            continue
        else: kind = rule.kind.upper()
        result.append(f'{kind},{rule.value},{target}' + (',no-resolve' if rule.kind == 'ip-cidr' else ''))
    return result


def library_targets(raw):
    return {rule.value: rule.target if rule.target in ('DIRECT', 'REJECT', GROUP) else group_name(rule.target)
            for rule in parse(raw).rules if rule.kind == 'library'}


def route_library(lines, target):
    """Change the route of a live library source without copying its conditions."""
    if target is None: return lines
    result = []
    for line in lines:
        suffix = ',no-resolve' if line.endswith(',no-resolve') else ''
        body = line[:-len(suffix)] if suffix else line
        result.append(body.rsplit(',', 1)[0] + ',' + target + suffix)
    return result


def groups(raw, rendered, routing):
    policy = parse(raw)
    if not policy.groups: return []
    available = {str(connection['id']): [name for name in (direct, cdn, tls) if name] for connection, direct, cdn, tls in rendered if connection.get('id')}
    all_names = [name for _, direct, cdn, tls in rendered for name in (direct, cdn, tls) if name and not name.startswith('__312_transport_')]
    result = []
    for group in policy.groups:
        members = []
        for member in group.members:
            if member == '*': members += all_names
            elif member.startswith('group:'): members.append(group_name(member[6:]))
            elif member[11:] in available: members += available[member[11:]]
            else: raise HTTPException(422, 'Группа ссылается на удалённое или недоступное подключение: ' + member[11:])
        # Never add a DIRECT escape hatch when the VPN group is empty.
        entry = {'name': group_name(group.name), 'type': group.type, 'proxies': list(dict.fromkeys(members)) or ['REJECT']}
        if group.type != 'select':
            entry.update(url=group.url if group.url is not None else routing.get('test_url', 'https://www.gstatic.com/generate_204'),
                interval=group.interval if group.interval is not None else int(routing.get('interval', 30)), lazy=False,
                timeout=group.timeout if group.timeout is not None else int(routing.get('health_timeout', 3000)),
                **{'max-failed-times': group.max_failed_times if group.max_failed_times is not None else int(routing.get('max_failed_times', 2))})
            if group.type == 'url-test': entry['tolerance'] = group.tolerance if group.tolerance is not None else int(routing.get('tolerance', 0))
        if group.type == 'load-balance': entry['strategy'] = group.strategy
        result.append(entry)
    if any(group['name'] in all_names for group in result): raise HTTPException(422, 'Имя группы совпадает с подключением')
    return result
