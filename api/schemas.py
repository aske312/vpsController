"""Validated request bodies shared by the API endpoints."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class BootstrapRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AdminPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=16, max_length=128)
    confirm_password: str = Field(min_length=16, max_length=128)


class SshPublicKeyInstall(BaseModel):
    public_key: str = Field(min_length=80, max_length=2048)


class ApplicationAction(BaseModel):
    action: Literal["restart", "update", "test-update", "test-rollback", "network-check", "integrity-check", "identity", "secure", "safe-update", "kernel-update", "vpn-firewall", "optimize", "reboot", "poweroff"]


class ServiceAction(BaseModel):
    action: Literal["start", "stop", "restart"]


class PanelAccessSettings(BaseModel):
    mode: Literal["external", "vpn"]


class CdnSecuritySettings(BaseModel):
    authenticated_origin_pulls: bool
    operation_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")


class ServiceModeSettings(BaseModel):
    active: bool


class LoggingSettings(BaseModel):
    persistent: bool
    retention_days: int = Field(ge=0, le=365)


class AutomationSchedule(BaseModel):
    enabled: bool
    cadence: Literal["daily", "weekly", "monthly"]
    weekday: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] = "Sun"
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class AutomationSettings(BaseModel):
    reboot: AutomationSchedule
    cleanup: AutomationSchedule
    update: AutomationSchedule


class DnsCustomResolver(BaseModel):
    name: str = Field(default="Собственный DNS", min_length=2, max_length=48)
    addresses: list[str] = Field(min_length=1, max_length=4)
    doh_url: str = Field(default="", max_length=256)


class DnsSettingsUpdate(BaseModel):
    selected_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    apply_wg: bool = True
    apply_awg: bool = True
    apply_shadowsocks: bool = True
    apply_vrx: bool = True
    prefer_encrypted: bool = False
    fallback_enabled: bool = True
    apply_system: bool = False
    custom: DnsCustomResolver | None = None


class DnsCheckRequest(BaseModel):
    provider_id: str | None = Field(default=None, max_length=64)


class ClientConnectionSettings(BaseModel):
    dns: str | None = Field(default=None, min_length=3, max_length=512, pattern=r"^[A-Za-z0-9:., ]+$")
    mtu: int | None = Field(default=None, ge=576, le=1500)
    keepalive: int | None = Field(default=None, ge=0, le=300)
    route_mode: Literal["all", "ipv4"] = "ipv4"
    shadowsocks_mode: Literal["tcp_only", "tcp_and_udp"] = "tcp_and_udp"
    timeout: int | None = Field(default=None, ge=30, le=3600)
    no_delay: bool = True
    fingerprint: Literal["chrome", "firefox", "safari"] = "chrome"


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[\w .-]+$")
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"]
    vless_routes: list[Literal["direct", "tls", "cdn"]] | None = None
    settings: ClientConnectionSettings = Field(default_factory=ClientConnectionSettings)


class ProtocolSettingsUpdate(BaseModel):
    mtu: int | None = Field(default=None, ge=1280, le=1420)
    timeout: int | None = Field(default=None, ge=30, le=3600)
    udp_mtu: int | None = Field(default=None, ge=576, le=1500)
    mode: Literal["tcp_only", "tcp_and_udp"] | None = None
    no_delay: bool | None = None
    xhttp_mode: Literal["auto", "stream-one", "stream-up", "packet-up"] | None = None
    transport: Literal["xhttp", "raw", "grpc"] | None = None
    transport_path: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
    dns: str | None = Field(default=None, min_length=3, max_length=512)
    keepalive: int | None = Field(default=None, ge=0, le=300)
    loglevel: Literal["debug", "info", "warning", "error", "none"] | None = None
    xpadding: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^\d+(?:-\d+)?$")
    sni: str | None = Field(default=None, min_length=4, max_length=253, pattern=r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
    xmux_concurrency: int | None = Field(default=None, ge=1, le=64)
    cdn_enabled: bool | None = None
    cdn_domain: str | None = Field(default=None, max_length=253)
    cdn_transport: Literal["websocket", "xhttp", "httpupgrade", "grpc"] | None = None
    cdn_xhttp_mode: Literal["auto", "stream-one", "stream-up", "packet-up"] | None = None
    tls_enabled: bool | None = None
    tls_domain: str | None = Field(default=None, max_length=253)
    tls_transport: Literal["websocket", "xhttp", "httpupgrade", "grpc"] | None = None
    tls_xhttp_mode: Literal["auto", "stream-one", "stream-up", "packet-up"] | None = None
    port: int | None = Field(default=None, ge=1024, le=65535)
    tls_mode: Literal["pinned", "acme"] | None = None
    domain: str | None = Field(default=None, max_length=253)
    obfs_enabled: bool | None = None
    obfs_password: str | None = Field(default=None, max_length=128)
    congestion_control: Literal["bbr", "cubic", "new_reno"] | None = None
    vpn_transport: Literal["udp", "tcp"] | None = None
