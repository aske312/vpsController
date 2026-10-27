"""Client-side routing catalogs. No server listeners or credentials are changed."""
import re
import ipaddress


def quic_rule(domain):
    return f"AND,((NETWORK,UDP),(DST-PORT,443),(DOMAIN-SUFFIX,{domain})),REJECT"


QUIC_GROUPS = {
    'youtube': ('youtube.com', 'googlevideo.com', 'youtubei.googleapis.com', 'ytimg.com'),
    'google': ('google.com', 'googleapis.com', 'gstatic.com'),
    'instagram': ('instagram.com', 'cdninstagram.com'),
    'facebook': ('facebook.com', 'fbcdn.net'),
    'tiktok': ('tiktok.com', 'tiktokcdn.com'),
}
QUIC_DEFAULT_RULES = [quic_rule(domain) for domain in QUIC_GROUPS['youtube']]
QUIC_AVAILABLE_RULES = list(dict.fromkeys(quic_rule(domain) for domains in QUIC_GROUPS.values() for domain in domains))
QUIC_RULE_PATTERN = re.compile(r'AND,\(\(NETWORK,UDP\),\(DST-PORT,443\),\(DOMAIN-SUFFIX,([a-z0-9.-]+)\)\),REJECT')


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
SERVICE_RULES = list(dict.fromkeys(f'DOMAIN-SUFFIX,{domain},DIRECT' for domains in SERVICE_GROUPS.values() for domain in domains))

# Exact-host feeds preserve the upstream treatment of shared hosting domains.
# TIF mini is the upstream's smaller threat feed for client devices.
# sing-box uses maintained URLhaus/phishing-filter SRS catalogs because its
# remote rule sets cannot consume these plain-domain text formats.
SECURITY_PROVIDERS = {
    'security-threats': {
        'url': 'https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif.mini-onlydomains.txt',
        'singbox_url': 'https://raw.githubusercontent.com/Chocolate4U/Iran-sing-box-rules/rule-set/geosite-malware.srs',
        'format': 'text', 'interval': 43200,
    },
    'security-phishing': {
        'url': 'https://phishing.army/download/phishing_army_blocklist_extended.txt',
        'singbox_url': 'https://raw.githubusercontent.com/Chocolate4U/Iran-sing-box-rules/rule-set/geosite-phishing.srs',
        'format': 'text', 'interval': 21600,
    },
}
