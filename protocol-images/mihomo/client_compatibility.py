"""Application verification is separate from format/exporter capabilities.

Each passed application test covers one exact configuration scenario and exact
app/core/OS versions. Parser tests and User-Agent recognition are not evidence.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit


MATRIX_FILE = Path(__file__).with_name("client-verification.json")
FORMATS = {"mihomo", "singbox", "xray", "uri"}
IDENTITY_FIELDS = ("client_name", "client_version", "core_name", "core_version", "os", "os_version")
PROTOCOL_OPTIONS = frozenset("bbr_profile cdn_xhttp_mode congestion_control cwnd disable_mtu_discovery down_mbps fingerprint handshake_timeout heartbeat initial_connection_receive_window initial_stream_receive_window ip_version jc jmax jmin keepalive max_connection_receive_window max_datagram_frame_size max_open_streams max_stream_receive_window max_udp_relay_packet_size method mptcp mtu name_cert_verify no_delay recv_window recv_window_conn skip_cert_verify tfo timeout tls_xhttp_mode udp_mtu udp_relay_mode up_mbps xhttp_mode xmux_concurrency xpadding zero_rtt".split())


def verified_records(path=None):
    try:
        matrix = json.loads((path or MATRIX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(matrix, dict) or matrix.get("version") != 1 or not isinstance(matrix.get("checks"), list):
        return []
    result = []
    for record in matrix["checks"]:
        if not isinstance(record, dict) or not all(isinstance(record.get(key), str) and record[key].strip() for key in (*IDENTITY_FIELDS, "id")):
            continue
        if record["os"] not in {"windows", "macos", "linux", "ios", "android"} or any(record[key].strip().lower() in {"*", "unknown", "latest"} for key in ("client_version", "core_version", "os_version")):
            continue
        evidence = record.get("evidence")
        scenario = record.get("scenario")
        if (not isinstance(record.get("format"), str) or record["format"] not in FORMATS or not isinstance(scenario, dict)
                or not isinstance(scenario.get("connections"), list) or not scenario["connections"]
                or not isinstance(evidence, dict) or evidence.get("kind") != "application"
                or evidence.get("result") != "passed" or not all(isinstance(evidence.get(key), str) and evidence[key].strip() for key in ("reference", "checked_at"))):
            continue
        if not isinstance(record.get("app_url"), str):
            continue
        try:
            link = urlsplit(record["app_url"])
        except ValueError:
            continue
        if link.scheme != "https" or not link.hostname or link.username or link.password:
            continue
        result.append(record)
    return result


def matching_records(scenario, records=None):
    return [record for record in (verified_records() if records is None else records) if record["scenario"] == scenario]


def select_format(metadata, scenario, records=None):
    for record in matching_records(scenario, records):
        if all(str(metadata.get(key, "")).casefold() == record[key].casefold() for key in IDENTITY_FIELDS):
            return record["format"]
    return "mihomo"


def public_record(record):
    return {key: record[key] for key in ("id", *IDENTITY_FIELDS, "format", "app_url")}
