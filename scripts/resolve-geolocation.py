#!/usr/bin/env python3
"""Resolve an IP location only when independent GeoIP sources agree."""

from collections import Counter
import json
import math
import re
import sys


MAX_CITY_CLUSTER_KM = 50.0
COUNTRY_CODES = {
    "belarus": "BY", "finland": "FI", "germany": "DE", "japan": "JP",
    "kazakhstan": "KZ", "latvia": "LV", "netherlands": "NL",
    "russia": "RU", "singapore": "SG", "spain": "ES", "sweden": "SE",
    "united states": "US", "united states of america": "US",
}
MAJOR_CITY_ANCHORS = (
    ("DE", "Frankfurt", 50.1109, 8.6821, 65), ("DE", "Berlin", 52.5200, 13.4050, 55),
    ("FI", "Helsinki", 60.1699, 24.9384, 55), ("SE", "Stockholm", 59.3293, 18.0686, 55),
    ("ES", "Madrid", 40.4168, -3.7038, 65), ("ES", "Barcelona", 41.3874, 2.1686, 55),
    ("KZ", "Astana", 51.1694, 71.4491, 65), ("KZ", "Almaty", 43.2220, 76.8512, 65),
    ("BY", "Minsk", 53.9006, 27.5590, 60), ("SG", "Singapore", 1.3521, 103.8198, 55),
    ("JP", "Tokyo", 35.6762, 139.6503, 70), ("LV", "Riga", 56.9496, 24.1052, 50),
    ("NL", "Amsterdam", 52.3676, 4.9041, 55), ("RU", "Moscow", 55.7558, 37.6173, 80),
    ("US", "Ashburn", 39.0438, -77.4874, 70), ("US", "New York", 40.7128, -74.0060, 70),
    ("US", "Chicago", 41.8781, -87.6298, 70), ("US", "Dallas", 32.7767, -96.7970, 80),
    ("US", "Los Angeles", 34.0522, -118.2437, 80), ("US", "Miami", 25.7617, -80.1918, 70),
)


def load(path):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError):
        return None


def coordinates(location):
    try:
        if location.get("loc"):
            latitude, longitude = str(location["loc"]).split(",", 1)
            return float(latitude), float(longitude)
        return (float(location.get("latitude") or location.get("lat")),
                float(location.get("longitude") or location.get("lon")))
    except (AttributeError, TypeError, ValueError):
        return None


def distance_km(first, second):
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    value = (math.sin((lat2 - lat1) / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def nearest_major_city(code, points):
    if not points:
        return "Unknown"
    center = (sum(point[0] for point in points) / len(points),
              sum(point[1] for point in points) / len(points))
    candidates = []
    for anchor_code, city, latitude, longitude, radius in MAJOR_CITY_ANCHORS:
        if anchor_code == code:
            distance = distance_km(center, (latitude, longitude))
            if distance <= radius:
                candidates.append((distance, city))
    return min(candidates)[1] if candidates else "Unknown"


def record(data):
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    location = data.get("location") if isinstance(data.get("location"), dict) else data
    network = data.get("network") if isinstance(data.get("network"), dict) else {}
    autonomous = network.get("autonomous_system") if isinstance(network.get("autonomous_system"), dict) else {}
    ip = str(data.get("ip") or data.get("ipAddress") or data.get("ip_address") or data.get("query") or "")
    city = str(location.get("city") or location.get("cityName") or location.get("city_name") or "").strip()
    country = str(location.get("country_name") or location.get("countryName") or location.get("country") or "").strip()
    raw_country = str(location.get("country") or "").strip()
    code = str(location.get("country_code") or location.get("countryCode") or
               location.get("country_code2") or data.get("country_code") or data.get("code") or
               autonomous.get("country") or (raw_country if re.fullmatch(r"[A-Za-z]{2}", raw_country) else "")).upper().strip()
    if not re.fullmatch(r"[A-Z]{2}", code):
        code = COUNTRY_CODES.get(country.casefold(), "")
    if not ip or not re.fullmatch(r"[A-Z]{2}", code):
        return None
    return ip, city, country, code, coordinates(location)


records = [item for item in (record(load(path)) for path in sys.argv[1:]) if item]
if not records:
    raise SystemExit(1)

ip = Counter(item[0] for item in records).most_common(1)[0][0]
records = [item for item in records if item[0] == ip]
code_votes = Counter(item[3] for item in records)
code, votes = code_votes.most_common(1)[0]
country_quorum = max(2, len(records) // 2 + 1)
if votes < country_quorum:
    raise SystemExit(1)

matching = [item for item in records if item[3] == code]
country = next((item[2] for item in matching if item[2] and not re.fullmatch(r"[A-Za-z]{2}", item[2])), code)
city_votes = Counter(item[1].casefold() for item in matching if item[1])
city_key, city_count = city_votes.most_common(1)[0] if city_votes else ("", 0)
city_quorum = max(2, len(matching) // 2 + 1)
if city_count >= city_quorum:
    city = next(item[1] for item in matching if item[1].casefold() == city_key)
else:
    clustered = set()
    for left in range(len(matching)):
        for right in range(left + 1, len(matching)):
            if (matching[left][4] and matching[right][4]
                    and distance_km(matching[left][4], matching[right][4]) <= MAX_CITY_CLUSTER_KM):
                clustered.update((left, right))
    city = nearest_major_city(code, [item[4] for index, item in enumerate(matching)
                                     if index in clustered and item[4]])

for value in (ip, city, country, code, f"{votes}/{len(records)}"):
    print(value)
