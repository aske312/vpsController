"""Client-side routing catalogs. No server listeners or credentials are changed."""
import re
import ipaddress
import json
from pathlib import Path
from profile_modes import VPN_SERVICES


# Dedicated advertising endpoints; broad shared infrastructure and tracker-only
# domains are excluded so advertising and tracker switches remain independent.
AD_SERVICE_CATALOG = json.loads(Path(__file__).with_name('ad-service-domains.json').read_text(encoding='utf-8'))
AD_RULES = [rule for group in AD_SERVICE_CATALOG['groups'] for rule in [
    *(f'DOMAIN-SUFFIX,{domain},REJECT' for domain in group['domains']),
    *(f'DOMAIN,{host},REJECT' for host in group['hosts']),
]]


# Reviewed service-owned domains; shared cloud/CDN hosts retain exact matching.
# The UI uses the same catalog so a service switch controls all of its assets.
RU_SERVICE_CATALOG = json.loads(Path(__file__).with_name('ru-service-domains.json').read_text(encoding='utf-8'))
RU_DIRECT_RULES = {}
for _group in RU_SERVICE_CATALOG['groups']:
    _rules = [f'DOMAIN-SUFFIX,{domain},DIRECT' for domain in _group['domains']]
    _rules += [f'DOMAIN,{host},DIRECT' for host in _group['hosts']]
    for _preset in _group['presets']:
        RU_DIRECT_RULES.setdefault(_preset, []).extend(_rules)


def quic_rule(domain, *, exact=False):
    kind = 'DOMAIN' if exact else 'DOMAIN-SUFFIX'
    return f"AND,((NETWORK,UDP),(DST-PORT,443),({kind},{domain})),REJECT"


# Curated service domains from v2fly/domain-list-community/data.
# Shared CDN parents are deliberately excluded; only service-specific names match.
QUIC_GROUPS = {
    'youtube': tuple(VPN_SERVICES['youtube']['domains']),
    'google': (
        'google.com', 'googleapis.com', 'gstatic.com',
    ),
    'instagram': tuple(VPN_SERVICES['instagram']['domains']),
    # Shared Meta media has one owner in the rule-group UI: Instagram/Reels.
    'facebook': tuple(domain for domain in VPN_SERVICES['facebook']['domains']
                      if domain not in VPN_SERVICES['instagram']['domains']),
    'tiktok': tuple(VPN_SERVICES['tiktok']['domains']),
    'twitch': (
        'twitch.tv', 'ttvnw.net', 'jtvnw.net',
        'twitchcdn.net', 'live-video.net',
    ),
    'netflix': (
        'netflix.com', 'netflix.net', 'nflxvideo.net',
        'nflxso.net', 'nflxext.com', 'nflximg.com',
        'nflximg.net', 'netflix.com.edgesuite.net',
    ),
    'vimeo': (
        'vimeo.com', 'vimeocdn.com', 'vimeoondemand.com',
        'vhx.tv', 'livestream.com',
    ),
    'dailymotion': (
        'dailymotion.com', 'dmcdn.net',
    ),
    'media_library': (
        'pornhub.com', 'pornhub.org', 'pornhubpremium.com',
        'phncdn.com', 'phprcdn.com', 'xvideos.com',
        'xv-ru.com', 'xvideos-cdn.com', 'xnxx.com',
        'xnxx.net', 'xnxx.tv', 'xnxx3.com',
        'xnxx-cdn.com', 'xhamster.com', 'xhamster.desi',
        'xhamster.xxx', 'xhamster18.com', 'xhamster19.com',
        'xhamster2.com', 'xhamster3.com', 'xhcdn.com',
        'xhpingcdn.com', 'youporn.com', 'youpornru.com',
        'youpornpremium.com', 'ypncdn.com', 'redtube.com',
        'redtubepremium.com', 'rdtcdn.com', 'spankbang.com',
        'spankbang.party', 'sb-cd.com',
    ),
}
QUIC_HOST_GROUPS = {group: tuple(VPN_SERVICES[group].get('hosts', []))
                    for group in ('youtube', 'instagram', 'tiktok')}


def quic_group_rules(group):
    return [*(quic_rule(domain) for domain in QUIC_GROUPS[group]),
            *(quic_rule(host, exact=True) for host in QUIC_HOST_GROUPS.get(group, ()))]


QUIC_DEFAULT_GROUPS = ('youtube', 'instagram', 'tiktok', 'media_library')
QUIC_DEFAULT_RULES = list(dict.fromkeys(rule for group in QUIC_DEFAULT_GROUPS for rule in quic_group_rules(group)))
QUIC_AVAILABLE_RULES = list(dict.fromkeys(rule for group in QUIC_GROUPS for rule in quic_group_rules(group)))
QUIC_RULE_PATTERN = re.compile(r'AND,\(\(NETWORK,UDP\),\(DST-PORT,443\),\((?:DOMAIN-SUFFIX|DOMAIN),([a-z0-9.-]+)\)\),REJECT')


def quic_domains(value):
    result = []
    for raw in re.split(r'[\s,]+', str(value).strip()):
        if not raw:
            continue
        try:
            domain = raw.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ValueError('Некорректный домен QUIC') from None
        if len(domain) > 253 or '.' not in domain or any(
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.')):
            raise ValueError('Укажите домены QUIC без URL, портов и масок')
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError('Укажите домен QUIC, а не IP-адрес')
        if domain not in result:
            result.append(domain)
    if len(result) > 256:
        raise ValueError('Допускается не больше 256 дополнительных доменов QUIC')
    return result


SERVICE_GROUPS = {
    'gosuslugi': ('gosuslugi.ru', 'gu-st.ru'),
    'government': ('gov.ru', 'government.ru', 'kremlin.ru', 'nalog.ru', 'pfr.gov.ru', 'sfr.gov.ru', 'fssp.gov.ru'),
    'regions': ('mos.ru', 'mosreg.ru', 'gu.spb.ru', 'gov.spb.ru'),
    'mts': ('mts.ru', 'mgts.ru'),
    'beeline': ('beeline.ru',),
    'megafon': ('megafon.ru', 'yota.ru'),
    't2': ('t2.ru', 'tele2.ru'),
    'rostelecom': ('rt.ru', 'rostelecom.ru', 'onlime.ru'),
    'providers': ('domru.ru', 'ertelecom.ru', 'ttk.ru'),
}
SERVICE_RULES = list(dict.fromkeys([
    *(f'DOMAIN-SUFFIX,{domain},DIRECT' for domains in SERVICE_GROUPS.values() for domain in domains),
    *RU_DIRECT_RULES['direct_ru_services'],
]))

# Exact-host feeds preserve the upstream treatment of shared hosting domains.
# TIF mini is the upstream's smaller threat feed for client devices.
SECURITY_PROVIDERS = {
    'security-threats': {
        'url': 'https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif.mini-onlydomains.txt',
        'format': 'text', 'interval': 43200,
    },
    'security-phishing': {
        'url': 'https://phishing.army/download/phishing_army_blocklist_extended.txt',
        'format': 'text', 'interval': 21600,
    },
}
