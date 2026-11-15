"""Structured rules/groups stored in the existing scalar routing contract."""
import ipaddress
import json
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

GROUP = 'GATE.312'
KINDS = ('domain', 'domain-suffix', 'ip-cidr', 'geoip', 'geosite', 'process')
GEO_CATALOG = {
    **{'geoip-' + name: {'behavior': 'ipcidr', 'format': 'text', 'interval': 86400,
        'url': f'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/{name}.list'} for name in ('cn', 'ru')},
    **{'geosite-' + name: {'behavior': 'domain', 'format': 'text', 'interval': 86400,
        'url': f'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/{name}.list'} for name in ('cn', 'youtube', 'telegram', 'github')},
}


class Rule(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['domain', 'domain-suffix', 'ip-cidr', 'geoip', 'geosite', 'process']
    value: str = Field(min_length=1, max_length=253)
    target: str = Field(default=GROUP, min_length=1, max_length=48)


class Group(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')
    type: Literal['select', 'fallback', 'url-test', 'load-balance'] = 'fallback'
    members: list[str] = Field(default_factory=lambda: ['*'], min_length=1, max_length=100)
    strategy: Literal['consistent-hashing', 'round-robin', 'sticky-sessions'] = 'consistent-hashing'


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
        for rule in policy.rules:
            if rule.target not in ('DIRECT', 'REJECT', GROUP, *names): raise ValueError('Правило ссылается на неизвестную группу')
            if rule.kind in ('domain', 'domain-suffix'):
                if not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', rule.value) or '..' in rule.value:
                    raise ValueError('Укажите домен без URL')
            elif rule.kind == 'ip-cidr': rule.value = str(ipaddress.ip_network(rule.value, strict=False))
            elif rule.kind in ('geoip', 'geosite'):
                if rule.kind + '-' + rule.value not in GEO_CATALOG: raise ValueError('Набор отсутствует в управляемом Geo-каталоге')
            elif not re.fullmatch(r'[a-zA-Z0-9_.@+ -]{1,160}', rule.value): raise ValueError('Недопустимое имя процесса')
        return policy
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, 'Некорректная политика маршрутизации: ' + (str(exc)[:180] if not isinstance(exc, ValidationError) else 'проверьте поля правил и групп')) from None


def canonical(raw): return parse(raw).model_dump_json()
def group_name(name): return 'policy:' + name


def rules(raw, *, mode=None, os=None):
    result = []
    for rule in parse(raw).rules:
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


def groups(raw, rendered, routing):
    policy = parse(raw)
    if not policy.groups: return []
    available = {str(connection['id']): [name for name in (direct, cdn, tls) if name] for connection, direct, cdn, tls in rendered if connection.get('id')}
    all_names = [name for _, direct, cdn, tls in rendered for name in (direct, cdn, tls) if name]
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
        if group.type != 'select': entry.update(url=routing.get('test_url', 'https://www.gstatic.com/generate_204'), interval=int(routing.get('interval', 30)), lazy=False,
            timeout=int(routing.get('health_timeout', 3000)), **{'max-failed-times': int(routing.get('max_failed_times', 2))})
        if group.type == 'load-balance': entry['strategy'] = group.strategy
        result.append(entry)
    if any(group['name'] in all_names for group in result): raise HTTPException(422, 'Имя группы совпадает с подключением')
    return result
