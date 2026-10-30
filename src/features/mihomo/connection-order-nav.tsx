import { useEffect, useRef, useState } from "react";
import type { Module, ProfileConnection } from "./types";
import { ProtocolIcon } from "../../shared/components/protocol-icon";

function connectionLabel(connection: ProfileConnection, modules: Module[]) {
  const settings = connection.settings;
  const mode = String(settings.route_mode || (settings.cdn_enabled ? "cdn" : "direct"));
  if (connection.component !== "transport-reality") return `${modules.find(module => module.id === connection.component)?.name || connection.component.replace("transport-", "")} · ${settings.relay_host_id ? "Relay" : "Прямое"}`;
  const protocol = ({ vless: "VLESS", vmess: "VMess", trojan: "Trojan" } as Record<string, string>)[String(settings.xray_protocol || "vless")] || "Xray";
  const transport = String(settings[mode === "cdn" ? "cdn_transport" : mode === "tls" ? "tls_transport" : "transport"] || "");
  return [protocol, mode === "cdn" ? "CDN" : mode === "tls" ? "TLS" : mode === "both" ? "REALITY + CDN" : "REALITY", transport.toUpperCase(), settings.relay_host_id ? "Relay" : ""].filter(Boolean).join(" · ");
}

export function ConnectionOrderNav({ connections, modules, selectedId, disabled, onSelect, onReorder }: {
  connections: ProfileConnection[];
  modules: Module[];
  selectedId?: string;
  disabled: boolean;
  onSelect: (id: string) => void;
  onReorder: (sourceId: string, targetId: string) => void;
}) {
  const nav = useRef<HTMLElement>(null);
  const gesture = useRef<{ id: string; pointerId: number; x: number; y: number; targetId: string; rect: DOMRect } | null>(null);
  const [drag, setDrag] = useState<{ id: string; targetId: string; x: number; y: number } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const selectedIndex = Math.max(0, connections.findIndex(item => item.id === selectedId));

  useEffect(() => {
    const list = nav.current;
    const tile = list?.querySelectorAll<HTMLElement>("[data-connection-id]")[selectedIndex];
    if (!list || !tile) return;
    const bounds = list.getBoundingClientRect(), selected = tile.getBoundingClientRect();
    const offset = selected.left < bounds.left ? selected.left - bounds.left
      : selected.right > bounds.right ? selected.right - bounds.right : 0;
    if (offset) list.scrollBy({ left: offset });
  }, [selectedIndex, connections.length]);

  function select(index: number) {
    if (disabled || !connections[index]) return;
    onSelect(connections[index].id);
  }

  function move(sourceId: string, targetId: string) {
    if (disabled || sourceId === targetId) return;
    onReorder(sourceId, targetId);
    setAnnouncement(`${connections.find((item) => item.id === sourceId)?.name}: приоритет ${connections.findIndex((item) => item.id === targetId) + 1}`);
  }

  return <div className="mihomoConnectionOrder">
    <div className="mihomoConnectionCarousel">
    <button type="button" className="mihomoConnectionArrow" aria-label="Предыдущее подключение" disabled={disabled || selectedIndex === 0} onClick={() => select(selectedIndex - 1)}><span aria-hidden="true">‹</span></button>
    <nav ref={nav} className="mihomoConnectionNav" aria-label="Подключения выбранного устройства">
      {connections.map((connection, index) => <div key={connection.id} data-connection-id={connection.id}
        className={`mihomoConnectionTile${selectedId === connection.id ? " is-active" : ""}${drag?.id === connection.id ? " is-dragging" : ""}${drag && drag.id !== connection.id && drag.targetId === connection.id ? " is-drop-target" : ""}`}
        style={drag?.id === connection.id ? { transform: `translate(${drag.x}px, ${drag.y}px)` } : undefined}>
        <button type="button" className="mihomoConnectionSelect" disabled={disabled} aria-pressed={selectedId === connection.id} title={connection.name} onClick={() => select(index)}
          onKeyDown={event => {
            const next = event.key === "Home" ? 0 : event.key === "End" ? connections.length - 1
              : event.key === "ArrowLeft" ? index - 1 : event.key === "ArrowRight" ? index + 1 : undefined;
            if (next === undefined) return;
            event.preventDefault();
            select(next);
            if (!disabled && connections[next]) nav.current?.querySelectorAll<HTMLButtonElement>(".mihomoConnectionSelect")[next]?.focus({ preventScroll: true });
          }}>
          <span className={`protocol-${connection.component}`}><ProtocolIcon protocol={connection.component} /></span>
          <span><b>{connection.name}</b>{connection.route_state?.state === "stale" && <small title={connection.route_state.reason}>{connection.route_state.addresses.length ? `Stale · ${connection.route_state.addresses.join(", ")}` : connection.route_state.reason}</small>}
            <small title={connectionLabel(connection, modules)}>{connectionLabel(connection, modules)}</small>
            </span><em aria-label={`Приоритет ${index + 1}`}>{index + 1}</em>
        </button>
        {connections.length > 1 && <button type="button" className="mihomoConnectionDrag" disabled={disabled}
          aria-label={`Изменить приоритет: ${connection.name}`} title="Перетащите или используйте клавиши со стрелками"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              if (gesture.current) event.stopPropagation();
              gesture.current = null; setDrag(null); return;
            }
            const offset = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : 0;
            if (!offset) return;
            event.preventDefault();
            const target = connections[index + offset];
            if (target) move(connection.id, target.id);
          }}
          onPointerDown={(event) => {
            if (disabled || !event.isPrimary || event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.focus();
            event.currentTarget.setPointerCapture(event.pointerId);
            gesture.current = { id: connection.id, pointerId: event.pointerId, x: event.clientX, y: event.clientY, targetId: connection.id, rect: event.currentTarget.parentElement!.getBoundingClientRect() };
          }}
          onPointerMove={(event) => {
            const active = gesture.current;
            if (!active || active.pointerId !== event.pointerId) return;
            const x = event.clientX - active.x, y = event.clientY - active.y;
            if (!drag && Math.hypot(x, y) < 5) return;
            const list = nav.current;
            if (list && list.scrollWidth > list.clientWidth) {
              const bounds = list.getBoundingClientRect();
              if (event.clientX < bounds.left + 28) list.scrollLeft -= 16;
              else if (event.clientX > bounds.right - 28) list.scrollLeft += 16;
            }
            const origin = active.rect;
            if (event.clientX >= origin.left && event.clientX <= origin.right && event.clientY >= origin.top && event.clientY <= origin.bottom) active.targetId = active.id;
            for (const tile of nav.current?.querySelectorAll<HTMLElement>("[data-connection-id]") || []) {
              if (tile.dataset.connectionId === active.id) continue;
              const rect = tile.getBoundingClientRect();
              if (event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) active.targetId = tile.dataset.connectionId!;
            }
            setDrag({ id: active.id, targetId: active.targetId, x, y });
          }}
          onPointerUp={(event) => {
            const active = gesture.current;
            if (!active || active.pointerId !== event.pointerId) return;
            gesture.current = null;
            setDrag(null);
            move(active.id, active.targetId);
          }}
          onPointerCancel={() => { gesture.current = null; setDrag(null); }}
          onLostPointerCapture={() => { gesture.current = null; setDrag(null); }}
        ><span aria-hidden="true">⠿</span></button>}
      </div>)}
    </nav>
    <button type="button" className="mihomoConnectionArrow" aria-label="Следующее подключение" disabled={disabled || selectedIndex >= connections.length - 1} onClick={() => select(selectedIndex + 1)}><span aria-hidden="true">›</span></button>
    </div>
    <div className="mihomoConnectionPosition" aria-live="polite">{connections.length ? selectedIndex + 1 : 0} / {connections.length}</div>
    <span className="mihomoConnectionOrderStatus" role="status">{announcement}</span>
  </div>;
}
