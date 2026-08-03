"use client";

import { useState } from "react";

const APP_URL = "https://storage.googleapis.com/amnezia/amnezia.org";

const protocolInstructions = [
  {
    id: "wg",
    name: "WireGuard",
    config: "*-wg.conf",
    identifyRu: "В файле есть секции [Interface] и [Peer], но нет полей Jc, Jmin, Jmax, S1–S4, H1–H4 или I1–I5.",
    identifyEn: "The file contains [Interface] and [Peer] sections without Jc, Jmin, Jmax, S1–S4, H1–H4 or I1–I5 fields.",
    installRu: "Установите AmneziaWG, выберите «Добавить туннель» → «Импорт из файла», укажите полученный конфиг и включите туннель. Дополнительные параметры вручную не добавляйте.",
    installEn: "Install AmneziaWG, select Add tunnel → Import from file, choose the received config and enable the tunnel. Do not add extra parameters manually.",
  },
  {
    id: "awg",
    name: "AmneziaWG",
    config: "*-awg.conf",
    identifyRu: "В секции [Interface] присутствуют параметры обфускации: Jc, Jmin, Jmax, S1–S4, H1–H4 или I1–I5.",
    identifyEn: "The [Interface] section includes obfuscation fields such as Jc, Jmin, Jmax, S1–S4, H1–H4 or I1–I5.",
    installRu: "Установите AmneziaWG, выберите «Добавить туннель» → «Импорт из файла», укажите полученный конфиг и включите туннель. Параметры обфускации уже записаны в файле — не изменяйте их.",
    installEn: "Install AmneziaWG, select Add tunnel → Import from file, choose the received config and enable the tunnel. Obfuscation parameters are already stored in the file—do not change them.",
  },
] as const;

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
      <section><span>03</span><div><h3>{ru ? "Скачайте конфигурацию" : "Download the configuration"}</h3><p>{ru ? "Сохраните файл сразу: приватный ключ повторно не показывается. Не редактируйте и не публикуйте содержимое конфигурации." : "Save the file immediately: its private key is not shown again. Do not edit or publish the configuration contents."}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Передайте владельцу" : "Send it to the owner"}</h3><p>{ru ? "Передайте файл владельцу указанного устройства по защищённому личному каналу. Не отправляйте его в общий чат и не размещайте по публичной ссылке." : "Send the file to the named device owner through a secure private channel. Never post it in a group chat or publish it through a public link."}</p></div></section>
      <section><span>05</span><div><h3>{ru ? "Одно подключение — один ключ" : "One connection, one key"}</h3><p>{ru ? "Не используйте одну конфигурацию на нескольких устройствах. Для каждого устройства создавайте новое подключение. Потерянный или скомпрометированный файл отзовите и выпустите заново." : "Never reuse one configuration across multiple devices. Create a new connection for each device. Revoke and replace any lost or compromised file."}</p></div></section>
    </div>

    <div className="protocolGuideHead">
      <p className="eyebrow">PROTOCOL INSTRUCTIONS</p>
      <h3>{ru ? "Как определить конфигурацию и установить её" : "How to identify and install a configuration"}</h3>
      <p>{ru ? "Используйте инструкцию именно для протокола, указанного при создании подключения. Для новых протоколов здесь будет добавляться отдельный способ установки." : "Follow the instructions for the protocol selected when the connection was created. Every new protocol will have its own installation instructions here."}</p>
    </div>
    <div className="protocolGuideGrid">
      {protocolInstructions.map((protocol) => <section className="protocolGuideCard" key={protocol.id}>
        <header><span className={`protocol ${protocol.id}`}>{protocol.id.toUpperCase()}</span><div><h3>{protocol.name}</h3><code>{protocol.config}</code></div></header>
        <div><strong>{ru ? "Как распознать" : "How to identify"}</strong><p>{ru ? protocol.identifyRu : protocol.identifyEn}</p></div>
        <div><strong>{ru ? "Как установить" : "How to install"}</strong><p>{ru ? protocol.installRu : protocol.installEn}</p></div>
        <a href={APP_URL} target="_blank" rel="noreferrer">{ru ? "Скачать приложение" : "Download the app"} ↗</a>
      </section>)}
    </div>
  </article>;
}
