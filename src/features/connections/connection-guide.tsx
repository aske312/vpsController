"use client";

import { useState } from "react";

const APP_URL = "https://storage.googleapis.com/amnezia/amnezia.org";

export function ConnectionGuide() {
  const [language, setLanguage] = useState<"ru" | "en">("ru");
  const ru = language === "ru";

  return <article className="connectionGuide">
    <header>
      <div><p className="eyebrow">CONNECTION WORKFLOW</p><h2>{ru ? "Передача нового подключения" : "Sharing a new connection"}</h2><span>{ru ? "Короткая памятка для безопасной выдачи доступа устройству." : "A compact checklist for safely issuing device access."}</span></div>
      <div className="guideLanguage"><button className={ru ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button><button className={!ru ? "active" : ""} onClick={() => setLanguage("en")}>EN</button></div>
    </header>
    <div className="connectionGuideSteps">
      <section><span>01</span><div><strong>{ru ? "Устройство и протокол" : "Device and protocol"}</strong><p>{ru ? "Создайте отдельное подключение с понятным именем и выберите один из установленных протоколов." : "Create a separate connection with a clear device name and select one installed protocol."}</p></div></section>
      <section><span>02</span><div><strong>{ru ? "Файл или QR" : "File or QR"}</strong><p>{ru ? "Передайте конфигурацию владельцу по личному защищённому каналу или покажите QR-код непосредственно на устройстве." : "Send the configuration through a private secure channel or show the QR code directly to the device owner."}</p></div></section>
      <section><span>03</span><div><strong>{ru ? "Один ключ — одно устройство" : "One key per device"}</strong><p>{ru ? "Не используйте одну конфигурацию повторно. Потерянный или скомпрометированный доступ отзовите и создайте заново." : "Do not reuse a configuration. Revoke and replace any lost or compromised access."}</p></div></section>
    </div>
    <footer><span>{ru ? "Клиент ещё не установлен?" : "Client not installed yet?"}</span><a href={APP_URL} target="_blank" rel="noreferrer">{ru ? "Открыть страницу установки" : "Open installation page"}</a></footer>
  </article>;
}
