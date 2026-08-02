"use client";

import { useState } from "react";

export function ConnectionGuide() {
  const [language, setLanguage] = useState<"ru" | "en">("ru");
  const ru = language === "ru";
  return <article className="panel connectionGuide">
    <div className="panelHead">
      <div><p className="eyebrow">CLIENT GUIDE</p><h2>{ru ? "Создание и подключение клиента" : "Create and connect a client"}</h2></div>
      <div className="guideLanguage"><button className={ru ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button><button className={!ru ? "active" : ""} onClick={() => setLanguage("en")}>EN</button></div>
    </div>
    <div className="guideSteps">
      <section><span>01</span><div><h3>{ru ? "Установите AmneziaWG" : "Install AmneziaWG"}</h3><p>{ru ? "Для подключений WG и AWG используйте одно приложение — AmneziaWG." : "Use the same AmneziaWG app for both WG and AWG connections."}</p><a href="https://storage.googleapis.com/amnezia/amnezia.org" target="_blank" rel="noreferrer">{ru ? "Скачать приложение ↗" : "Download the app ↗"}</a></div></section>
      <section><span>02</span><div><h3>{ru ? "Создайте клиента" : "Create a client"}</h3><p>{ru ? "Введите понятное уникальное имя, выберите протокол и нажмите «Создать конфигурацию». Один клиент — одно устройство." : "Enter a clear unique name, select a protocol and click “Create configuration”. Use one client per device."}</p></div></section>
      <section><span>03</span><div><h3>{ru ? "Сохраните конфигурацию" : "Save the configuration"}</h3><p>{ru ? "Скачайте .conf сразу: приватный ключ повторно не показывается. Передавайте файл только владельцу устройства по защищённому каналу." : "Download the .conf file immediately: the private key is not shown again. Send it only to the device owner over a secure channel."}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Импортируйте и включите" : "Import and activate"}</h3><p>{ru ? "Импортируйте .conf в AmneziaWG. Для WG обязательно включите галочку «Обфускация», затем сохраните и включите подключение." : "Import the .conf file into AmneziaWG. For WG, enable the “Obfuscation” checkbox, then save and activate the connection."}</p></div></section>
      <section><span>05</span><div><h3>{ru ? "Отзовите при утрате" : "Revoke if compromised"}</h3><p>{ru ? "Если устройство или файл потеряны, нажмите «Отозвать» и создайте нового клиента. Не используйте одну конфигурацию на нескольких устройствах." : "If a device or file is lost, click “Revoke” and create a new client. Never reuse one configuration on multiple devices."}</p></div></section>
    </div>
  </article>;
}
