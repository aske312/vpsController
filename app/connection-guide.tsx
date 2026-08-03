"use client";

import { useState } from "react";

const APP_URL = "https://storage.googleapis.com/amnezia/amnezia.org";

export function ConnectionGuide() {
  const [language, setLanguage] = useState<"ru" | "en">("ru");
  const ru = language === "ru";

  return <article className="panel connectionGuide">
    <div className="panelHead">
      <div><p className="eyebrow">CONNECTION WORKFLOW</p><h2>{ru ? "Как создать и передать новое подключение" : "How to create and share a new connection"}</h2></div>
      <div className="guideLanguage"><button className={ru ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button><button className={!ru ? "active" : ""} onClick={() => setLanguage("en")}>EN</button></div>
    </div>
    <p className="guideIntro">{ru
      ? "Администратор создаёт отдельную конфигурацию для конкретного устройства и передаёт владельцу только скачанный файл .conf. Для WG и AWG используется одно приложение — AmneziaWG."
      : "The administrator creates a separate configuration for a specific device and sends only the downloaded .conf file to its owner. The same AmneziaWG app is used for WG and AWG."}</p>
    <div className="guideSteps">
      <section><span>01</span><div><h3>{ru ? "Назовите устройство" : "Name the device"}</h3><p>{ru ? "Укажите понятное уникальное имя: например, iPhone Анны или Office-PC. Так подключение можно будет найти и отозвать." : "Use a clear unique name, such as Anna's iPhone or Office-PC, so the connection can be found and revoked later."}</p></div></section>
      <section><span>02</span><div><h3>{ru ? "Выберите протокол" : "Select a protocol"}</h3><p>{ru ? "Выберите установленный WG или AWG и нажмите «Создать конфигурацию». Панель создаст персональный ключ для этого устройства." : "Select an installed WG or AWG protocol and click Create configuration. The panel creates a personal key for this device."}</p></div></section>
      <section><span>03</span><div><h3>{ru ? "Скачайте файл" : "Download the file"}</h3><p>{ru ? "Сохраните .conf сразу: приватный ключ повторно не показывается. Не редактируйте содержимое файла." : "Save the .conf immediately: its private key is not shown again. Do not edit the file contents."}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Передайте владельцу" : "Send it to the owner"}</h3><p>{ru ? "Передайте файл только владельцу указанного устройства по защищённому личному каналу. Не отправляйте его в общий чат и не публикуйте ссылкой." : "Send the file only to the owner of the named device through a secure private channel. Never post it in a group chat or publish it as a link."}</p></div></section>
      <section><span>05</span><div><h3>{ru ? "Один ключ — одно устройство" : "One key, one device"}</h3><p>{ru ? "Нельзя устанавливать один .conf на несколько устройств. Для каждого устройства создавайте отдельное подключение; при утрате файла нажмите «Отозвать» и выпустите новое." : "Never install one .conf on multiple devices. Create a separate connection for every device; if the file is lost, revoke it and issue a new one."}</p><a href={APP_URL} target="_blank" rel="noreferrer">AmneziaWG ↗</a></div></section>
    </div>
  </article>;
}
