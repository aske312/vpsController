"use client";

import Image from "next/image";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import type { Protocol } from "./page";
import { ProtocolIcon } from "./protocol-icon";

export type ConnectionProfile = {
  protocol: Protocol;
  name: string;
  endpoint: string;
  fields: Array<{ label: string; value: string; secret?: boolean }>;
  apps: string[];
  steps: string[];
  one_time: boolean;
  delivery: {
    file: { filename: string; content: string; mime_type: string };
    link?: { uri: string; label: string } | null;
    qr?: { content: string; label: string } | null;
  };
};

export const protocolDelivery: Record<Protocol, {
  title: string;
  summary: string;
  transport: string;
  apps: string;
  methods: string[];
}> = {
  wg: { title: "WireGuard", summary: "Классический VPN-туннель для одного устройства.", transport: "UDP · .conf", apps: "WireGuard", methods: ["QR", "Файл"] },
  awg: { title: "AmneziaWG", summary: "Обфусцированный WireGuard-профиль с параметрами сервера.", transport: "UDP · .conf", apps: "AmneziaWG", methods: ["QR", "Файл"] },
  hysteria2: { title: "Hysteria2", summary: "Персональная учётная запись с закреплённым TLS-сертификатом.", transport: "QUIC · YAML", apps: "Hiddify, NekoBox, Hysteria 2", methods: ["QR", "Ссылка", "Файл"] },
  tuic: { title: "TUIC v5", summary: "Индивидуальные UUID и пароль в готовом профиле sing-box.", transport: "QUIC · JSON", apps: "sing-box, NekoBox", methods: ["Файл"] },
  xray: { title: "Xray VLESS", summary: "Персональный VLESS UUID с транспортом XHTTP + REALITY.", transport: "TCP · XHTTP", apps: "Hiddify, v2rayN, NekoBox", methods: ["QR", "Ссылка", "Файл"] },
};

type Props = {
  profile: ConnectionProfile;
  onDownload(filename: string, content: string, mimeType?: string): void;
};

export function ConnectionProfileResult({ profile, onDownload }: Props) {
  const [qrResult, setQrResult] = useState({ content: "", url: "" });
  const [copied, setCopied] = useState("");
  const meta = protocolDelivery[profile.protocol];
  const qrContent = profile.delivery.qr?.content || "";
  const qr = qrResult.content === qrContent ? qrResult.url : "";

  useEffect(() => {
    let active = true;
    if (qrContent) {
      void QRCode.toDataURL(qrContent, {
        width: 440,
        margin: 2,
        errorCorrectionLevel: "M",
        color: { dark: "#081216", light: "#ffffff" },
      }).then((value) => { if (active) setQrResult({ content: qrContent, url: value }); });
    }
    return () => { active = false; };
  }, [qrContent]);

  async function copy(value: string, key: string) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      const field = document.createElement("textarea");
      field.value = value;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      document.execCommand("copy");
      field.remove();
    }
    setCopied(key);
    window.setTimeout(() => setCopied((current) => current === key ? "" : current), 1600);
  }

  return <div className="connectionResult">
    <div className="connectionResultHead">
      <span className={`protocol ${profile.protocol}`}><ProtocolIcon protocol={profile.protocol} /></span>
      <div><p className="eyebrow">ПРОФИЛЬ СОЗДАН · ПОКАЗЫВАЕТСЯ ОДИН РАЗ</p><h3>{profile.name}</h3><small>{meta.title} · {profile.endpoint}</small></div>
    </div>

    <div className={`connectionHandoff ${qr ? "withQr" : ""}`}>
      {qr && <div className="connectionQr"><Image src={qr} width={220} height={220} unoptimized alt={`QR-код подключения ${profile.name}`} /><small>{profile.delivery.qr?.label}</small></div>}
      <div className="connectionActions">
        {profile.delivery.link && <button type="button" className="handoffAction primary" onClick={() => void copy(profile.delivery.link!.uri, "link")}><span>↗</span><div><strong>{copied === "link" ? "Ссылка скопирована" : profile.delivery.link.label}</strong><small>Персональная ссылка · не публикуйте её</small></div></button>}
        <button type="button" className="handoffAction" onClick={() => onDownload(profile.delivery.file.filename, profile.delivery.file.content, profile.delivery.file.mime_type)}><span>↓</span><div><strong>Скачать профиль</strong><small>{profile.delivery.file.filename}</small></div></button>
        <button type="button" className="handoffAction" onClick={() => void copy(profile.delivery.file.content, "config")}><span>⌘</span><div><strong>{copied === "config" ? "Содержимое скопировано" : "Копировать конфигурацию"}</strong><small>Для ручного импорта</small></div></button>
      </div>
    </div>

    <div className="connectionCredentials">
      {profile.fields.map((field) => <div key={field.label}><small>{field.label}</small><strong>{field.value}</strong>{field.secret && <button type="button" onClick={() => void copy(field.value, field.label)}>{copied === field.label ? "готово" : "копировать"}</button>}</div>)}
    </div>

    <div className="connectionSetup">
      <div><small>РЕКОМЕНДУЕМЫЕ КЛИЕНТЫ</small><strong>{profile.apps.join(" · ")}</strong></div>
      <ol>{profile.steps.map((step) => <li key={step}>{step}</li>)}</ol>
    </div>

    <details><summary>Техническое содержимое профиля <span>⌄</span></summary><textarea readOnly value={profile.delivery.file.content} /></details>
    <p className="connectionSecretNote">После закрытия этого блока секреты нельзя будет показать повторно. При утрате профиля отзовите подключение и создайте новое.</p>
  </div>;
}
