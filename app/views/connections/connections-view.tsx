"use client";

import type { Dispatch, SetStateAction } from "react";
import { ConnectionGuide } from "../../connection-guide";
import { bytes, CLIENTS_PER_PAGE, duration, labels } from "../../lib/control-plane-ui";
import type { Client, DeviceProbe, Protocol } from "../../types/control-plane";

type ClientStateFilter = "all" | "online" | "attention" | "offline";
type ConnectionsViewProps = {
  installedProtocols: Protocol[]; clientStateFilter: ClientStateFilter; setClientStateFilter: Dispatch<SetStateAction<ClientStateFilter>>; setClientPage: Dispatch<SetStateAction<number>>;
  clientSummary: { total: number; online: number; attention: number; offline: number }; deviceProbe: DeviceProbe | null; probingDevice: boolean;
  measureDeviceRoute: (force?: boolean) => Promise<void> | void; openClientDialog: () => void; protocolClients: Client[]; clientSearch: string; setClientSearch: Dispatch<SetStateAction<string>>;
  clientProtocolFilter: "all" | Protocol; setClientProtocolFilter: Dispatch<SetStateAction<"all" | Protocol>>; currentClientPage: number; clientPageCount: number; visibleClientStart: number; visibleClientEnd: number; visibleClients: Client[];
  removeClient: (id: string) => Promise<void> | void;
};

export function ConnectionsView({ installedProtocols, clientStateFilter, setClientStateFilter, setClientPage, clientSummary, deviceProbe, probingDevice, measureDeviceRoute, openClientDialog, protocolClients, clientSearch, setClientSearch, clientProtocolFilter, setClientProtocolFilter, currentClientPage, clientPageCount, visibleClientStart, visibleClientEnd, visibleClients, removeClient }: ConnectionsViewProps) {
  return <section className="connectionsWorkspace">
        <article className="connectionsHero">
          <div className="connectionsHeroContent">
            <div className="connectionsHeroCopy">
              <p className="eyebrow">312.NET / ACCESS CONTROL</p>
              <h1>Подключения</h1>
              <p>Устройства, ключи доступа и фактическое состояние прямых подключений к узлу.</p>
            </div>
            <div className="connectionsHeroStats">
              <button className={clientStateFilter === "all" ? "active" : ""} onClick={() => { setClientStateFilter("all"); setClientPage(1); }}><small>ВСЕ</small><strong>{clientSummary.total}</strong></button>
              <button className={`${clientStateFilter === "online" ? "active " : ""}online`} onClick={() => { setClientStateFilter("online"); setClientPage(1); }}><small>ОНЛАЙН</small><strong>{clientSummary.online}</strong></button>
              <button className={`${clientStateFilter === "attention" ? "active " : ""}attention`} onClick={() => { setClientStateFilter("attention"); setClientPage(1); }}><small>ВНИМАНИЕ</small><strong>{clientSummary.attention}</strong></button>
              <button className={clientStateFilter === "offline" ? "active offline" : "offline"} onClick={() => { setClientStateFilter("offline"); setClientPage(1); }}><small>ОФЛАЙН</small><strong>{clientSummary.offline}</strong></button>
            </div>
            <div className="connectionsProbeStrip">
              <div className="connectionsProbePrimary">
                <small>THIS DEVICE / PANEL</small>
                <strong>{deviceProbe?.latency_ms != null ? `${deviceProbe.latency_ms} мс` : "—"}</strong>
                <span>{deviceProbe?.route || "текущий маршрут браузера"}</span>
              </div>
              <dl>
                <div><dt>УСПЕШНО</dt><dd>{deviceProbe ? `${deviceProbe.successful}/${deviceProbe.samples}` : "—"}</dd></div>
                <div><dt>ПОТЕРИ</dt><dd>{deviceProbe ? `${deviceProbe.loss_percent}%` : "—"}</dd></div>
                <div><dt>РАЗБРОС RTT</dt><dd>{deviceProbe?.variation_ms != null ? `${deviceProbe.variation_ms} мс` : "—"}</dd></div>
              </dl>
              <button type="button" onClick={() => void measureDeviceRoute(true)} disabled={probingDevice}>{probingDevice ? "Измеряем…" : "Измерить"}</button>
            </div>
            <div className="connectionsHeroActions">
              <a href="/connection-guide-wg-awg.pdf" download>Скачать гайд PDF</a>
              <button className="primaryButton" onClick={openClientDialog}>Новое подключение</button>
            </div>
          </div>
          <div className="connectionsHeroArt" aria-hidden="true" />
        </article>

        <article className="connectionsInventory">
          <header className="connectionsInventoryHead">
            <div><p className="eyebrow">ACCESS INVENTORY</p><h2>Устройства и доступ</h2><span>Одно подключение соответствует одному устройству и отдельному ключу.</span></div>
            <div><span><b>{protocolClients.length}</b> найдено</span><span><b>{installedProtocols.length}</b> протокола</span></div>
          </header>
          <div className="connectionsFilters">
            <label><span>Поиск</span><input type="search" value={clientSearch} onChange={(event) => { setClientSearch(event.target.value); setClientPage(1); }} placeholder="Имя, адрес или протокол" /></label>
            <label><span>Протокол</span><select value={clientProtocolFilter} onChange={(event) => { setClientProtocolFilter(event.target.value as "all" | Protocol); setClientPage(1); }}><option value="all">Все протоколы</option>{installedProtocols.map((protocol) => <option key={protocol} value={protocol}>{labels[protocol]}</option>)}</select></label>
            {(clientSearch || clientProtocolFilter !== "all" || clientStateFilter !== "all") && <button type="button" onClick={() => { setClientSearch(""); setClientProtocolFilter("all"); setClientStateFilter("all"); setClientPage(1); }}>Сбросить</button>}
          </div>
          {protocolClients.length > CLIENTS_PER_PAGE && <nav className="connectionsPagination" aria-label="Страницы подключений"><span>{visibleClientStart}–{visibleClientEnd} из {protocolClients.length}</span><div><button onClick={() => setClientPage(Math.max(1, currentClientPage - 1))} disabled={currentClientPage === 1}>Назад</button><strong>{currentClientPage} / {clientPageCount}</strong><button onClick={() => setClientPage(Math.min(clientPageCount, currentClientPage + 1))} disabled={currentClientPage === clientPageCount}>Дальше</button></div></nav>}
          <div className="connectionsRows">
            {protocolClients.length ? visibleClients.map((client) => {
              const clientStateLabel = client.quality === "stable" ? "ОНЛАЙН" : client.quality === "offline" ? "ОФЛАЙН" : "НЕСТАБИЛЬНО";
              const protocolLabel = client.protocol === "wg" ? "WG" : client.protocol === "awg" ? "AWG" : client.protocol === "shadowsocks" ? "SS" : "VRX";
              return <div className={`connectionRow quality-${client.quality || "offline"}`} key={client.id}>
                <div className="connectionIdentity"><span className={`protocol ${client.protocol}`}>{protocolLabel}</span><div><strong>{client.name}</strong><small>{client.address}{client.active_sources?.length ? ` · ${client.active_sources.join(", ")}` : ""}</small></div></div>
                <div className="connectionState"><span className={`connectionStateBadge ${client.quality || "offline"}`}>{clientStateLabel}</span><small>{client.quality_reason || "состояние уточняется"}</small></div>
                <div className="connectionActivity"><small>{client.protocol === "wg" || client.protocol === "awg" ? "HANDSHAKE" : "АКТИВНОСТЬ"}</small><strong>{duration(client.handshake_age_s)}</strong><span>{client.active_connections ? `${client.active_connections} активн.` : "нет активных"}</span></div>
                <div className="connectionTraffic"><span><small>RX</small><strong>{bytes(client.rx_bytes)}</strong><em>{bytes(client.rx_bps)}/с</em></span><span><small>TX</small><strong>{bytes(client.tx_bytes)}</strong><em>{bytes(client.tx_bps)}/с</em></span><span><small>RTT</small><strong>{client.latency_ms !== undefined && client.latency_ms !== null ? `${client.latency_ms} мс` : "—"}</strong><em>{client.packet_loss_percent !== undefined && client.packet_loss_percent !== null ? `${client.packet_loss_percent}% loss` : client.latency_source === "server_icmp_tunnel_ip" ? "VPS → device" : "недоступен"}</em></span></div>
                <button className="connectionRevoke" onClick={() => void removeClient(client.id)}>Отозвать</button>
              </div>;
            }) : <div className="connectionsEmpty"><strong>Подключений не найдено</strong><span>Измените фильтры или создайте новое подключение.</span></div>}
          </div>
        </article>
        <ConnectionGuide />
      </section>;
}
