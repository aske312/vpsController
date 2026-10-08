"use client";

import { useState } from "react";
import type { Protocol } from "./page";

type ClientOS = "windows" | "ios" | "android";
type ClientSource = { label: string; href: string; direct?: boolean };
type ClientApp = { name: string; formats: string; note: string; sources: ClientSource[] };

const osLabels: Record<ClientOS, string> = { windows: "Windows", ios: "iOS", android: "Android" };
const karing = (os: ClientOS): ClientApp => ({ name: "Karing", formats: "URI · QR · sing-box JSON", note: "Hysteria2 и TUIC v5. Для полного JSON проверьте версию ядра; расширения QUIC требуют 1.14+.", sources: os === "ios" ? [{ label: "App Store РФ", href: "https://apps.apple.com/ru/app/karing/id6472431552" }] : [{ label: os === "android" ? "GitHub APK" : "GitHub Releases", href: "https://github.com/KaringX/karing/releases/latest", direct: true }] });
const singboxIos: ClientApp = { name: "sing-box MT", formats: "Полный JSON · VPN / TUN", note: "Официальный клиент. Импортируйте файл с TUN и DNS; один mixed-прокси не даёт VPN на iPhone. Российский каталог Apple проверен 09.10.2026.", sources: [{ label: "App Store РФ", href: "https://apps.apple.com/ru/app/sing-box-mt/id6785326793" }] };
const amnezia = (os: "windows" | "android"): ClientApp => ({ name: "AmneziaVPN", formats: ".conf · AmneziaWG", note: "Официальный универсальный клиент. Для CPS-сигнатур нужен актуальный клиент с поддержкой AWG 2.0.", sources: [{ label: os === "android" ? "GitHub APK" : "GitHub Releases", href: "https://github.com/amnezia-vpn/amnezia-client/releases/latest", direct: true }] });
const happ = (os: "windows" | "android"): ClientApp => ({ name: "Happ", formats: "VLESS URI · QR · XHTTP", note: `Клиент на Xray-core для ${osLabels[os]}. После импорта проверьте XHTTP mode и extra.`, sources: [{ label: "Официальные загрузки", href: "https://www.happ.su/main" }] });
const hiddifyWindows: ClientApp = { name: "Hiddify", formats: "URI · QR · JSON", note: "Windows-версия доступна напрямую с GitHub, независимо от App Store РФ.", sources: [{ label: "GitHub Releases", href: "https://github.com/hiddify/hiddify-app/releases/latest", direct: true }] };

export const clientApps: Record<Protocol, Record<ClientOS, ClientApp[]>> = {
  awg: {
    windows: [{ name: "AmneziaWG", formats: ".conf", note: "Официальный нативный клиент; основной вариант.", sources: [{ label: "GitHub Releases", href: "https://github.com/amnezia-vpn/amneziawg-windows-client/releases/latest", direct: true }] }, amnezia("windows")],
    ios: [
      { name: "AmneziaWG", formats: ".conf", note: "Официальный клиент для iPhone и iPad.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/amneziawg/id6478942365" }] },
      { name: "DefaultVPN", formats: ".conf", note: "Альтернатива для iOS 16 и новее.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/defaultvpn/id6744725017" }] },
    ],
    android: [{ name: "AmneziaWG", formats: ".conf", note: "APK доступен напрямую, если Google Play недоступен.", sources: [{ label: "GitHub APK", href: "https://github.com/amnezia-vpn/amneziawg-android/releases/latest", direct: true }, { label: "Google Play", href: "https://play.google.com/store/apps/details?id=org.amnezia.awg" }] }, amnezia("android")],
  },
  hysteria2: {
    windows: [karing("windows"), hiddifyWindows, { name: "Hysteria 2 CLI", formats: "YAML · локальный прокси", note: "Штатный клиент для полного набора Hysteria QUIC/BBR/Fast Open/Lazy. Не является графическим приложением.", sources: [{ label: "GitHub Releases", href: "https://github.com/apernet/hysteria/releases/latest", direct: true }] }],
    ios: [karing("ios"), singboxIos],
    android: [
      karing("android"),
      { name: "NekoBox", formats: "URI · QR", note: "Скачивайте только из официального GitHub; версия Google Play не принадлежит проекту.", sources: [{ label: "GitHub APK", href: "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest", direct: true }] },
    ],
  },
  tuic: {
    windows: [
      karing("windows"),
      hiddifyWindows,
      { name: "sing-box CLI", formats: "JSON · TUN или mixed", note: "Официальный бинарник для Windows; для TUN нужны права администратора. Не графическое приложение.", sources: [{ label: "GitHub Releases", href: "https://github.com/SagerNet/sing-box/releases/latest", direct: true }] },
    ],
    ios: [singboxIos, karing("ios")],
    android: [
      { name: "sing-box for Android", formats: "JSON", note: "Официальные APK входят в релизы sing-box.", sources: [{ label: "GitHub APK", href: "https://github.com/SagerNet/sing-box/releases/latest", direct: true }] },
      karing("android"),
    ],
  },
  xray: {
    windows: [
      { name: "v2rayN", formats: "URI · QR", note: "Основной вариант для XHTTP: использует Xray-core и сохраняет режим stream-one.", sources: [{ label: "GitHub Releases", href: "https://github.com/2dust/v2rayN/releases/latest", direct: true }] },
      happ("windows"),
    ],
    ios: [
      { name: "EnigmaPlus", formats: "VLESS · XHTTP", note: "Доступен в каталоге Apple РФ. Разработчик заявляет XHTTP + REALITY; импорт нашего профиля и extra на устройстве пока не проверен.", sources: [{ label: "App Store РФ", href: "https://apps.apple.com/ru/app/enigmaplus/id6780630921" }] },
      { name: "DefaultVPN", formats: "URI · QR", note: "Доступная альтернатива для iOS 16 и новее; проверьте режим XHTTP после импорта.", sources: [{ label: "App Store", href: "https://apps.apple.com/app/defaultvpn/id6744725017" }] },
    ],
    android: [
      { name: "v2rayNG", formats: "URI · QR", note: "Основной Android-клиент на Xray-core с поддержкой VLESS XHTTP REALITY.", sources: [{ label: "GitHub APK", href: "https://github.com/2dust/v2rayNG/releases/latest", direct: true }] },
      happ("android"),
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
