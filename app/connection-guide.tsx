"use client";

import { useState } from "react";

type Language = "ru" | "en";
type GuideProtocol = "wg" | "awg";

const protocols: Array<{
  id: GuideProtocol;
  short: string;
  name: string;
  app: string;
  appUrl: string;
  identify: Record<Language, string>;
  import: Record<Language, string[]>;
}> = [
  {
    id: "wg",
    short: "WG",
    name: "WireGuard",
    app: "WireGuard",
    appUrl: "https://www.wireguard.com/install/",
    identify: {
      ru: "Имя файла обычно заканчивается на -wg.conf. Внутри есть [Interface] и [Peer], но нет строк Jc, Jmin, Jmax, S1-S4, H1-H4 или I1-I5.",
      en: "The filename usually ends in -wg.conf. It contains [Interface] and [Peer], but no Jc, Jmin, Jmax, S1-S4, H1-H4, or I1-I5 fields.",
    },
    import: {
      ru: ["Установите официальное приложение WireGuard по ссылке ниже.", "Откройте приложение и выберите импорт туннеля из файла (на телефоне нажмите +).", "Выберите полученный .conf, подтвердите создание VPN и включите туннель. Стандартный WG-конфиг не требует ручного включения обфускации."],
      en: ["Install the official WireGuard app using the link below.", "Open the app and choose to import a tunnel from a file (tap + on mobile).", "Select the received .conf, approve VPN creation, and activate the tunnel. A standard WG configuration does not require manually enabled obfuscation."],
    },
  },
  {
    id: "awg",
    short: "AWG",
    name: "AmneziaWG",
    app: "AmneziaWG",
    appUrl: "https://storage.googleapis.com/amnezia/amnezia.org",
    identify: {
      ru: "Имя файла обычно заканчивается на -awg.conf. В секции [Interface] есть дополнительные поля обфускации: Jc, Jmin, Jmax, S1, S2 и H1-H4 (в будущих версиях также могут быть S3-S4 или I1-I5).",
      en: "The filename usually ends in -awg.conf. Its [Interface] section has extra obfuscation fields: Jc, Jmin, Jmax, S1, S2, and H1-H4 (future versions may also include S3-S4 or I1-I5).",
    },
    import: {
      ru: ["Установите официальное приложение AmneziaWG по ссылке ниже.", "Откройте приложение, нажмите «Добавить туннель» или + и выберите импорт из файла.", "Выберите полученный .conf и включите туннель. Параметры обфускации уже записаны в файл - вручную ничего включать не нужно."],
      en: ["Install the official AmneziaWG app using the link below.", "Open it, choose Add Tunnel or +, and import from a file.", "Select the received .conf and activate the tunnel. Obfuscation parameters are already in the file; no manual option is required."],
    },
  },
];

export function ConnectionGuide() {
  const [language, setLanguage] = useState<Language>("ru");
  const [selected, setSelected] = useState<GuideProtocol>("wg");
  const ru = language === "ru";
  const protocol = protocols.find((item) => item.id === selected) ?? protocols[0];

  return <article className="panel connectionGuide">
    <div className="panelHead">
      <div><p className="eyebrow">CLIENT GUIDE</p><h2>{ru ? "Как подключить полученную конфигурацию" : "How to connect with the received configuration"}</h2></div>
      <div className="guideLanguage"><button className={ru ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button><button className={!ru ? "active" : ""} onClick={() => setLanguage("en")}>EN</button></div>
    </div>

    <div className="guideProtocolTabs" role="tablist" aria-label={ru ? "Выбор протокола" : "Select protocol"}>
      {protocols.map((item) => <button key={item.id} role="tab" aria-selected={selected === item.id} className={selected === item.id ? "active" : ""} onClick={() => setSelected(item.id)}><span className={`protocol ${item.id}`}>{item.short}</span><strong>{item.name}</strong></button>)}
    </div>

    <div className="guideIdentification">
      <strong>{ru ? "Как понять, какой конфиг вам дали" : "How to identify your configuration"}</strong>
      <p>{protocol.identify[language]}</p>
      <small>{ru ? "Сначала смотрите на окончание имени файла. Если файл переименовали - проверьте только названия полей, не копируя ключи и содержимое в чат." : "Check the filename suffix first. If it was renamed, inspect field names only; never paste keys or file contents into chat."}</small>
    </div>

    <div className="guideSteps protocolSpecific">
      <section><span>01</span><div><h3>{ru ? `Приложение для ${protocol.short}` : `App for ${protocol.short}`}</h3><p>{protocol.import[language][0]}</p><a href={protocol.appUrl} target="_blank" rel="noreferrer">{protocol.app} ↗</a></div></section>
      <section><span>02</span><div><h3>{ru ? "Импорт конфигурации" : "Import configuration"}</h3><p>{protocol.import[language][1]}</p></div></section>
      <section><span>03</span><div><h3>{ru ? "Подключение" : "Connect"}</h3><p>{protocol.import[language][2]}</p></div></section>
      <section><span>04</span><div><h3>{ru ? "Проверка" : "Verify"}</h3><p>{ru ? "Статус должен стать активным, счётчики трафика - изменяться, а сайты - открываться. Если нет, выключите туннель на 10 секунд и включите снова." : "The status should become active, traffic counters should change, and websites should open. If not, turn the tunnel off for 10 seconds and retry."}</p></div></section>
      <section><span>05</span><div><h3>{ru ? "Безопасность" : "Security"}</h3><p>{ru ? "Один конфиг предназначен для одного устройства. При утрате устройства или файла попросите администратора отозвать подключение и выдать новое." : "Use one configuration on one device only. If the device or file is lost, ask the administrator to revoke it and issue a new one."}</p></div></section>
    </div>
  </article>;
}
