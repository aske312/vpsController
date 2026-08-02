"use client";

import { useEffect, useState, type ReactNode } from "react";

type LegalDocument = "privacy" | "terms";
type Language = "ru" | "en";
type LegalSection = [title: string, content: ReactNode];
type LocalizedDocument = { label: string; eyebrow: string; notice: ReactNode; sections: LegalSection[] };

const documents: Record<Language, Record<LegalDocument, LocalizedDocument>> = {
  ru: {
    privacy: {
      label: "Уведомление о приватности",
      eyebrow: "SELF-HOSTED · 02.08.2026",
      notice: <>312.net — свободное self-hosted ПО. Проект не предоставляет централизованный облачный сервис и не получает встроенный административный доступ к установленному экземпляру.</>,
      sections: [
        ["Где находятся данные", <>Конфигурации, ключи, журналы, сведения о сервере, модулях и соединениях обрабатываются на VPS, которым управляет пользователь. Данные сессии панели могут временно храниться в sessionStorage браузера администратора. Встроенной отправки содержимого экземпляра участникам проекта нет.</>],
        ["Внешние соединения", <>Установка, обновления, пакетные репозитории, GeoIP и сетевая диагностика могут обращаться к GitHub и другим сторонним ресурсам. Такие ресурсы получают IP-адрес и технические метаданные запроса и применяют собственные политики.</>],
        ["Ответственность администратора", <>Лицо или организация, управляющие конкретным экземпляром, самостоятельно определяют цели и способы обработки данных. Если применимое право требует уведомления о приватности, правового основания, сроков удаления, защиты данных или обработки запросов пользователей, администратор экземпляра обеспечивает это самостоятельно.</>],
        ["Добровольные обращения", <>Сообщения об ошибках, предложения и иные материалы передаются проекту только по инициативе отправителя. Не включайте в публичные обращения пароли, приватные ключи, токены, персональные данные и другие секреты.</>],
      ],
    },
    terms: {
      label: "Свободная лицензия",
      eyebrow: "MIT LICENSE · 02.08.2026",
      notice: <>312.net распространяется по лицензии MIT. Юридически определяющим является полный текст файла LICENSE в составе проекта.</>,
      sections: [
        ["Разрешения", <>Разрешается безвозмездно использовать, копировать, изменять, объединять, публиковать, распространять, сублицензировать и продавать копии ПО, а также разрешать это другим лицам при сохранении уведомления об авторских правах и текста лицензии MIT.</>],
        ["Независимый экземпляр", <>ПО является инструментом управления собственным VPS. Участники проекта не становятся хостинг-провайдером, VPN-оператором, оператором связи или администратором установленного пользователем экземпляра и не управляют его трафиком, ключами и соединениями.</>],
        ["Действия пользователя", <>Лицензия на ПО не предоставляет разрешения на доступ к чужим системам и не отменяет требования закона, договора с хостинг-провайдером или права третьих лиц. Пользователь самостоятельно отвечает за выбранные серверы, команды, конфигурации и способы применения ПО.</>],
        ["Сторонние компоненты", <>Операционная система, библиотеки, модули, GitHub, пакетные репозитории, GeoIP и другие сторонние ресурсы могут иметь собственные лицензии и условия. MIT применяется к 312.net и не заменяет условия таких компонентов.</>],
        ["Отсутствие гарантий", <>ПО предоставляется «как есть», без каких-либо гарантий. В пределах, допускаемых применимым правом, авторы и правообладатели не несут ответственности по искам, за ущерб или иные требования, возникшие из ПО, его использования или иных действий с ним. Обязательные нормы применимого права сохраняют силу.</>],
      ],
    },
  },
  en: {
    privacy: {
      label: "Privacy Notice",
      eyebrow: "SELF-HOSTED · 2 AUG 2026",
      notice: <>312.net is free self-hosted software. The project does not provide a central cloud service or receive built-in administrative access to an installed instance.</>,
      sections: [
        ["Where data resides", <>Configurations, keys, logs and server, module and connection details are processed on the VPS controlled by the user. Panel session data may be stored temporarily in the administrator browser&apos;s sessionStorage. Instance content is not transmitted to project contributors by default.</>],
        ["External connections", <>Installation, updates, package repositories, GeoIP and network diagnostics may contact GitHub and other third-party resources. Those resources receive the IP address and technical request metadata and apply their own policies.</>],
        ["Instance administrator", <>The person or organisation controlling an instance independently determines the purposes and means of any data processing. Where applicable law requires a privacy notice, legal basis, deletion periods, safeguards or responses to data-subject requests, the instance administrator must provide them.</>],
        ["Voluntary submissions", <>Bug reports, suggestions and other materials reach the project only when a sender chooses to submit them. Do not include passwords, private keys, tokens, personal data or other secrets in public submissions.</>],
      ],
    },
    terms: {
      label: "Free Software License",
      eyebrow: "MIT LICENSE · 2 AUG 2026",
      notice: <>312.net is distributed under the MIT License. The complete LICENSE file included with the project is the legally controlling text.</>,
      sections: [
        ["Permission", <>Any person may, free of charge, use, copy, modify, merge, publish, distribute, sublicense and sell copies of the Software, and permit others to do so, provided that the copyright notice and MIT permission notice are included.</>],
        ["Independent instance", <>The Software is a tool for administering a user-controlled VPS. Project contributors do not thereby become a hosting provider, VPN operator, communications provider or administrator of the installed instance and do not control its traffic, keys or connections.</>],
        ["User actions", <>The software license does not authorise access to third-party systems or override law, hosting agreements or third-party rights. Users remain responsible for the servers, commands, configurations and uses they choose.</>],
        ["Third-party components", <>The operating system, libraries, modules, GitHub, package repositories, GeoIP and other third-party resources may have their own licences and terms. MIT applies to 312.net and does not replace those terms.</>],
        ["No warranty", <>The Software is provided “as is”, without warranty of any kind. To the extent permitted by applicable law, authors and copyright holders are not liable for claims, damages or other liability arising from the Software, its use or other dealings in it. Mandatory applicable law remains unaffected.</>],
      ],
    },
  },
};

export function LegalFooter({ version, commit }: { version: string; commit: string }) {
  const [openDocument, setOpenDocument] = useState<LegalDocument | null>(null);
  const [language, setLanguage] = useState<Language>("ru");

  useEffect(() => {
    if (!openDocument) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setOpenDocument(null);
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [openDocument]);

  const current = openDocument ? documents[language][openDocument] : null;
  return <>
    <footer className="versionFooter">
      <span>{version} build {commit}</span>
      <nav aria-label="Правовые документы / Legal documents">
        <button type="button" onClick={() => setOpenDocument("privacy")}>Приватность / Privacy</button>
        <button type="button" onClick={() => setOpenDocument("terms")}>Лицензия / License</button>
      </nav>
    </footer>
    {current && <div className="legalBackdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setOpenDocument(null);
    }}>
      <section className="legalScreen" role="dialog" aria-modal="true" aria-labelledby="legal-title">
        <header>
          <div><p className="eyebrow">{current.eyebrow}</p><h2 id="legal-title">{current.label}</h2></div>
          <button type="button" onClick={() => setOpenDocument(null)} aria-label="Закрыть / Close">×</button>
        </header>
        <div className="legalLanguage" role="group" aria-label="Language">
          <button className={language === "ru" ? "active" : ""} onClick={() => setLanguage("ru")}>RU</button>
          <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")}>EN</button>
        </div>
        <article>
          <p className="legalNotice">{current.notice}</p>
          {current.sections.map(([title, content], index) => <section key={title}>
            <h3>{index + 1}. {title}</h3><div>{content}</div>
          </section>)}
        </article>
        <footer><button type="button" onClick={() => setOpenDocument(null)}>{language === "ru" ? "Закрыть" : "Close"}</button></footer>
      </section>
    </div>}
  </>;
}
