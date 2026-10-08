"use client";

import { useState } from "react";
import type { Protocol } from "./page";
import { ClientAppCatalog } from "./client-apps";

const guides: Record<Protocol, {
  app: string; importRu: string; importEn: string; checkRu: string; checkEn: string;
}> = {
  awg: { app: "подходящий клиент AmneziaWG", importRu: "Отсканируйте QR или импортируйте .conf в AmneziaWG. Параметры обфускации уже включены в профиль.", importEn: "Scan the QR code or import the .conf file into AmneziaWG; obfuscation parameters are already included.", checkRu: "Включите профиль и проверьте работу через мобильную сеть и Wi‑Fi.", checkEn: "Activate the profile and test it on both mobile data and Wi-Fi." },
  hysteria2: { app: "совместимый клиент Hysteria2", importRu: "Откройте персональную Hysteria2-ссылку или QR в совместимом приложении. YAML предназначен для Hysteria CLI.", importEn: "Open the personal Hysteria2 link or QR in a compatible app. YAML is intended for the Hysteria CLI.", checkRu: "В приложении включите VPN/TUN. Hysteria CLI открывает только локальный SOCKS: направьте браузер в этот прокси. Затем проверьте внешний сайт и IP выхода.", checkEn: "Enable VPN/TUN in the app. The Hysteria CLI only opens local SOCKS: configure your browser to use it. Then check an external site and your exit IP." },
  tuic: { app: "клиент на базе sing-box", importRu: "Скачайте персональный JSON с UUID, паролем и закреплённым сертификатом. Используйте sing-box или приложение, принимающее полный sing-box JSON.", importEn: "Download the personal JSON containing the UUID, password, and pinned certificate. Use sing-box or an app that accepts a full sing-box JSON.", checkRu: "Профиль открывает локальный mixed-прокси. Направьте браузер в этот прокси либо включите VPN/TUN в приложении. Проверьте внешний сайт и IP выхода.", checkEn: "The profile opens a local mixed proxy. Configure your browser to use it or enable VPN/TUN in the app. Check an external site and your exit IP." },
  xray: { app: "совместимый клиент Xray", importRu: "Откройте VLESS-ссылку или QR в клиенте с Xray-core и поддержкой XHTTP + REALITY. JSON предназначен для Xray-core.", importEn: "Open the VLESS link or QR in a client with Xray-core and XHTTP + REALITY support. JSON is intended for Xray-core.", checkRu: "Включите системный прокси для браузера или TUN для всего устройства. При запуске Xray-core вручную настройте браузер на SOCKS/HTTP из профиля. Проверьте внешний сайт и IP выхода.", checkEn: "Enable the system proxy for your browser or TUN for the device. When running Xray-core manually, configure the browser to use the profile's SOCKS/HTTP proxy. Check an external site and your exit IP." },
};

export function ConnectionGuide({ protocol }: { protocol: Protocol }) {
  const [language, setLanguage] = useState<"ru" | "en">("ru");
  const ru = language === "ru";
  const guide = guides[protocol];
  return <article className="panel connectionGuide">
    <div className="panelHead">
      <div><p className="eyebrow">PERSONAL SETUP · {protocol.toUpperCase()}</p><h2>{ru ? "Настройка подключения для пользователя" : "Create and connect a client"}</h2></div>
      <div className="guideLanguage"><button type="button" className={ru ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button><button type="button" className={!ru ? "active" : ""} onClick={() => setLanguage("en")}>EN</button></div>
    </div>
    <div className="guideSteps protocolAwareGuide">
      <section><span>01</span><div><h3>{ru ? `Установите ${guide.app}` : `Install ${guide.app}`}</h3><p>{ru ? "Выберите приложение ниже по операционной системе. Для РФ предусмотрены прямые официальные загрузки без магазина." : "Choose an app below for the device OS. Direct official downloads are provided when an app store is unavailable."}</p></div></section>
      <section><span>02</span><div><h3>{ru ? "Передайте профиль" : "Hand off the profile"}</h3><p>{ru ? "Создайте отдельное подключение для конкретного человека или устройства. Секреты передавайте только по защищённому каналу." : "Create a separate connection for each person or device. Share secrets only through a secure channel."}</p></div></section>
      <section><span>03</span><div><h3>{ru ? "Импортируйте" : "Import"}</h3><p>{ru ? guide.importRu : guide.importEn}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Проверьте соединение" : "Verify the connection"}</h3><p>{ru ? guide.checkRu : guide.checkEn}</p></div></section>
      <section><span>05</span><div><h3>{ru ? "Отзовите при утрате" : "Revoke if compromised"}</h3><p>{ru ? "Если устройство или профиль потеряны, отзовите именно это подключение и создайте новое. Не используйте один профиль на нескольких устройствах." : "If the device or profile is lost, revoke this exact connection and create a new one. Never reuse one profile across devices."}</p></div></section>
    </div>
    <ClientAppCatalog protocol={protocol} />
  </article>;
}
