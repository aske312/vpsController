"use client";

import { useState } from "react";
import type { Protocol } from "./page";
import { ClientAppCatalog } from "./client-apps";

const guides: Record<Protocol, {
  app: string; importRu: string; importEn: string; checkRu: string; checkEn: string;
}> = {
  awg: { app: "подходящий клиент AmneziaWG", importRu: "Отсканируйте QR или импортируйте .conf в AmneziaWG. Параметры обфускации уже включены в профиль.", importEn: "Scan the QR code or import the .conf file into AmneziaWG; obfuscation parameters are already included.", checkRu: "Включите профиль и проверьте работу через мобильную сеть и Wi‑Fi.", checkEn: "Activate the profile and test it on both mobile data and Wi-Fi." },
  hysteria2: { app: "совместимый клиент Hysteria2", importRu: "Откройте персональную Hysteria2-ссылку, отсканируйте QR или импортируйте YAML-файл.", importEn: "Open the personal Hysteria2 link, scan the QR code, or import the YAML file.", checkRu: "Проверьте SNI и отпечаток сертификата, затем включите профиль.", checkEn: "Confirm the SNI and certificate fingerprint, then activate the profile." },
  tuic: { app: "клиент на базе sing-box", importRu: "Скачайте персональный JSON с UUID, паролем и закреплённым сертификатом, затем импортируйте его в клиент.", importEn: "Download the personal JSON containing the UUID, password, and pinned certificate, then import it into the client.", checkRu: "Запустите профиль и при необходимости включите системный VPN-режим приложения.", checkEn: "Start the profile and enable the app's system VPN mode if needed." },
  xray: { app: "совместимый клиент Xray", importRu: "Откройте VLESS-ссылку, отсканируйте QR или импортируйте JSON с XHTTP + REALITY.", importEn: "Open the VLESS link, scan the QR code, or import the XHTTP + REALITY JSON.", checkRu: "Убедитесь, что клиент сохранил REALITY server name и short ID, затем включите профиль.", checkEn: "Confirm the REALITY server name and short ID, then activate the profile." },
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
