"use client";

import { useState } from "react";
import type { Protocol } from "./page";

type ClientOS = "windows" | "ios" | "android";
type ClientSource = { label: string; href: string; direct?: boolean };
type ClientApp = { name: string; formats: string; note: string; sources: ClientSource[] };

const osLabels: Record<ClientOS, string> = { windows: "Windows", ios: "iOS", android: "Android" };

export const clientApps: Record<Protocol, Record<ClientOS, ClientApp[]>> = {
  awg: {
    windows: [{ name: "AmneziaWG", formats: ".conf", note: "Официальный нативный клиент; основной вариант.", sources: [{ label: "GitHub Releases", href: "https://github.com/amnezia-vpn/amneziawg-windows-client/releases/latest", direct: true }] }],
    ios: [
      { name: "AmneziaWG", formats: ".conf", note: "Официальный клиент для iPhone и iPad.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/amneziawg/id6478942365" }] },
      { name: "DefaultVPN", formats: ".conf", note: "Альтернатива для iOS 16 и новее.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/defaultvpn/id6744725017" }] },
    ],
    android: [{ name: "AmneziaWG", formats: ".conf", note: "APK доступен напрямую, если Google Play недоступен.", sources: [{ label: "GitHub APK", href: "https://github.com/amnezia-vpn/amneziawg-android/releases/latest", direct: true }, { label: "Google Play", href: "https://play.google.com/store/apps/details?id=org.amnezia.awg" }] }],
  },
  hysteria2: {
    windows: [{ name: "Hiddify", formats: "URI · QR · YAML", note: "Установщик и portable-архив публикуются напрямую.", sources: [{ label: "GitHub Releases", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] }],
    ios: [{ name: "Hiddify", formats: "URI · QR", note: "При региональном ограничении магазина доступен официальный IPA для ручной установки.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532" }, { label: "GitHub IPA", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] }],
    android: [
      { name: "Hiddify", formats: "URI · QR · YAML", note: "Рекомендуемый универсальный APK без зависимости от Google Play.", sources: [{ label: "GitHub APK", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] },
      { name: "NekoBox", formats: "URI · QR", note: "Скачивайте только из официального GitHub; версия Google Play не принадлежит проекту.", sources: [{ label: "GitHub APK", href: "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest", direct: true }] },
    ],
  },
  tuic: {
    windows: [
      { name: "Hiddify", formats: "JSON", note: "Импортирует профиль sing-box; доступна portable-версия.", sources: [{ label: "GitHub Releases", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] },
      { name: "sing-box Desktop", formats: "JSON", note: "Официальный клиент для Windows 10 и новее.", sources: [{ label: "GitHub Releases", href: "https://github.com/SagerNet/sing-box/releases/latest", direct: true }] },
    ],
    ios: [{ name: "Hiddify", formats: "JSON", note: "Основной доступный вариант; официальный sing-box для iOS может отсутствовать в магазине.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532" }, { label: "GitHub IPA", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] }],
    android: [
      { name: "sing-box for Android", formats: "JSON", note: "Официальные APK входят в релизы sing-box.", sources: [{ label: "GitHub APK", href: "https://github.com/SagerNet/sing-box/releases/latest", direct: true }] },
      { name: "NekoBox", formats: "JSON", note: "Поддерживает TUIC; используйте только официальный GitHub.", sources: [{ label: "GitHub APK", href: "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest", direct: true }] },
    ],
  },
  xray: {
    windows: [
      { name: "Hiddify", formats: "URI · QR · JSON", note: "Простой импорт и системный VPN-режим.", sources: [{ label: "GitHub Releases", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] },
      { name: "v2rayN", formats: "URI · QR", note: "Продвинутый клиент Windows 10+ с Xray-core.", sources: [{ label: "GitHub Releases", href: "https://github.com/2dust/v2rayN/releases/latest", direct: true }] },
    ],
    ios: [
      { name: "Hiddify", formats: "URI · QR", note: "Поддерживает VLESS, XHTTP и REALITY.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532" }, { label: "GitHub IPA", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] },
      { name: "DefaultVPN", formats: "URI · QR", note: "Альтернатива для iOS 16 и новее.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/defaultvpn/id6744725017" }] },
    ],
    android: [
      { name: "Hiddify", formats: "URI · QR · JSON", note: "Рекомендуемый APK с прямой загрузкой.", sources: [{ label: "GitHub APK", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] },
      { name: "NekoBox", formats: "URI · QR", note: "Поддерживает VLESS/REALITY; не используйте одноимённую версию Google Play.", sources: [{ label: "GitHub APK", href: "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest", direct: true }] },
    ],
  },
};

export function ClientAppCatalog({ protocol, compact = false }: { protocol: Protocol; compact?: boolean }) {
  const [os, setOs] = useState<ClientOS>("android");
  return <section className={`clientAppCatalog${compact ? " compact" : ""}`}>
    <header><div><strong>Приложения для подключения</strong><small>Выберите систему устройства</small></div><div className="clientOsPicker">{(Object.keys(osLabels) as ClientOS[]).map((item) => <button type="button" className={os === item ? "active" : ""} key={item} onClick={() => setOs(item)}>{osLabels[item]}</button>)}</div></header>
    <div className="clientAppRows">{clientApps[protocol][os].map((app) => <article key={app.name}><div><strong>{app.name}</strong><small>{app.formats}</small><p>{app.note}</p></div><nav>{app.sources.map((source) => <a className={source.direct ? "direct" : ""} key={source.href} href={source.href} target="_blank" rel="noreferrer">{source.label} ↗</a>)}</nav></article>)}</div>
    <p className="clientRegionNote">Для РФ сначала используйте прямую загрузку из официального GitHub. Не устанавливайте APK, IPA или EXE из каталогов и зеркал, не связанных с разработчиком.</p>
  </section>;
}
