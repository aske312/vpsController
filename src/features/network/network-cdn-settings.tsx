"use client";

import type { NetworkRequest } from "./network-api";
import { NetworkCloudflare, type NetworkCloudflareControl } from "./network-cloudflare";
import { NetworkEch } from "./network-ech";

export function NetworkCdnSettings({ domain, route, detected, request, control, refreshKey }: {
  domain: string; route: string; detected: boolean; request: NetworkRequest;
  control?: NetworkCloudflareControl; refreshKey: number;
}) {
  const throughCdn = route === "proxy_or_cdn";
  return <section className="routeTaskCard routeCdnSettings" aria-label="Защита CDN">
    {control && <NetworkCloudflare domain={domain} detected={detected} request={request} control={control} refreshKey={refreshKey} />}
    <NetworkEch domain={domain} route={route} request={request} />
    <details className="routeCdnHelp"><summary>Справка<span aria-hidden="true">+</span></summary><div>
      <h4>Проверка адреса</h4><p>Проверяются настроенные для этого адреса подключения Direct и Mihomo со стороны VPS. Доступность из вашей сети проверяется отдельно.</p>
      {control && <><h4>Сертификат Cloudflare</h4><p>Общая настройка CDN-маршрутов Cloudflare на этом VPS. Перед включением настройте Full (strict) и Authenticated Origin Pulls в Cloudflare.</p><p>Сертификат подтверждает сеть Cloudflare, но не ваш аккаунт. При неудачной проверке изменение откатится.</p></>}
      <h4>ECH</h4>
      {throughCdn ? <p>Включите ECH у CDN-провайдера. Запись и ключи VPS относятся к прямому TLS-соединению и для этого CDN-маршрута не нужны.</p> : <><p>Сейчас DNS не подтверждает маршрут через CDN. Подготовка ECH на VPS относится к прямому соединению.</p><h4>Как применить запись VPS</h4><ol><li>Добавьте HTTPS-запись у DNS-провайдера с именем и значением из блока ECH. A/AAAA сохраните.</li><li>Если панель дописывает зону, укажите только имя узла, например <code>cdn</code>. TTL — не ниже минимума провайдера.</li><li>После обновления DNS включите ECH в клиенте и обновите подписку.</li></ol><p>После смены ключей ECH обновите запись вручную.</p></>}
    </div></details>
  </section>;
}
