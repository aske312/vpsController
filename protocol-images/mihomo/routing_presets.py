"""Client-side routing catalogs. No server listeners or credentials are changed."""
import re
import ipaddress


def quic_rule(domain):
    return f"AND,((NETWORK,UDP),(DST-PORT,443),(DOMAIN-SUFFIX,{domain})),REJECT"


# Curated service domains from v2fly/domain-list-community/data.
# Shared CDN parents are deliberately excluded; only service-specific names match.
# Curated service domains from v2fly/domain-list-community/data.
# Shared CDN parents are deliberately excluded; only service-specific names match.
QUIC_GROUPS = {
    'youtube': (
        'youtube.com', 'googlevideo.com', 'youtubei.googleapis.com',
        'ytimg.com', 'youtube-nocookie.com', 'youtube.googleapis.com',
        'youtubeembeddedplayer.googleapis.com', 'youtu.be',
    ),
    'google': (
        'google.com', 'googleapis.com', 'gstatic.com',
    ),
    'instagram': (
        'instagram.com', 'cdninstagram.com',
    ),
    'facebook': (
        'facebook.com', 'fbcdn.net',
    ),
    'tiktok': (
        'tiktok.com', 'tiktokcdn.com', 'tiktokcdn-eu.com',
        'tiktokcdn-us.com', 'tiktokeu-cdn.com', 'tiktokrow-cdn.com',
        'tiktokv.com', 'tiktokv.eu', 'tiktokv.us',
        'ttlivecdn.com', 'tiktokcdn.com.akamaized.net', 'tiktokcdn.com.edgesuite.net',
        'tiktokcdn-us.com.edgesuite.net', 'tiktokv.com.edgekey.net', 'tiktokv.com.edgesuite.net',
    ),
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
    'vk_video': (
        'vkvideo.ru', 'vkvideo.com', 'vkuservideo.com',
        'vkuservideo.net', 'vkuservideo.ru', 'vkuserlive.com',
        'vkuserlive.net',
    ),
    'rutube': (
        'rutube.ru', 'rutubelist.ru', 'rtbcdn.ru',
        'rutube.sport',
    ),
    'ok_video': (
        'ok.ru', 'okcdn.ru',
    ),
    'kinopoisk': (
        'kinopoisk.ru', 'kinopoisk-ru.clstorage.net', 'kp.yandex.net',
        'kp-config-smart-tv.akamaized.net',
    ),
    'okko': (
        'okko.tv', 'okkoapi.tv', 'playfamily.ru',
        'okko.sport',
    ),
    'wink': (
        'wink.ru', 'wink.am', 'restream-media.net',
        'restream.ru', 'more.tv',
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
