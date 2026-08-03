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
      ? "Каждое подключение создаётся для одного конкретного устройства. Выберите доступный протокол, скачайте созданную конфигурацию и передайте её только владельцу этого устройства."
      : "Each connection is created for one specific device. Select an available protocol, download the generated configuration and send it only to that device owner."}</p>
    <div className="guideSteps">
      <section><span>01</span><div><h3>{ru ? "Назовите устройство" : "Name the device"}</h3><p>{ru ? "Укажите понятное уникальное имя, например «iPhone Анны» или «Office-PC». По нему подключение можно будет найти и отозвать." : "Use a clear unique name, such as Anna's iPhone or Office-PC, so the connection can be found and revoked later."}</p></div></section>
      <section><span>02</span><div><h3>{ru ? "Выберите протокол" : "Select a protocol"}</h3><p>{ru ? "Выберите один из установленных на сервере протоколов и нажмите «Создать конфигурацию». Панель выпустит отдельный ключ." : "Select one of the protocols installed on the server and click Create configuration. The panel will issue a separate key."}</p></div></section>
      <section><span>03</span><div><h3>{ru ? "Передайте конфигурацию" : "Share the configuration"}</h3><p>{ru ? "Скачайте файл и передайте его владельцу по защищённому личному каналу либо покажите созданный QR-код на его устройстве. Приватный ключ повторно не показывается." : "Download the file and send it to the owner through a secure private channel, or let them scan the generated QR code on their device. The private key is not shown again."}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Отправьте ссылку на приложение" : "Send the app link"}</h3><p>{ru ? "Если приложение ещё не установлено, отдельно отправьте владельцу ссылку на его установку. Не публикуйте конфигурацию, файл или QR-код в общем чате." : "If the app is not installed yet, send its installation link separately. Never post the configuration file or QR code in a group chat."}</p><a href={APP_URL} target="_blank" rel="noreferrer">{ru ? "Ссылка на установку" : "Installation link"} ↗</a></div></section>
      <section><span>05</span><div><h3>{ru ? "Одно подключение — один ключ" : "One connection, one key"}</h3><p>{ru ? "Не используйте одну конфигурацию на нескольких устройствах. Для каждого устройства создавайте новое подключение. Потерянный или скомпрометированный файл отзовите и выпустите заново." : "Never reuse one configuration across multiple devices. Create a new connection for each device. Revoke and replace any lost or compromised file."}</p></div></section>
    </div>
  </article>;
}
