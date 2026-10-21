import { useRef, useState } from "react";
import type { Module, ProfileConnection } from "./types";
import { ProtocolIcon } from "../../shared/components/protocol-icon";

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

  function move(sourceId: string, targetId: string) {
    if (disabled || sourceId === targetId) return;
    onReorder(sourceId, targetId);
    setAnnouncement(`${connections.find((item) => item.id === sourceId)?.name}: приоритет ${connections.findIndex((item) => item.id === targetId) + 1}`);
  }

  return <div className="mihomoConnectionOrder">
    <nav ref={nav} className="mihomoConnectionNav" aria-label="Подключения выбранного устройства">
      {connections.map((connection, index) => <div key={connection.id} data-connection-id={connection.id}
        className={`mihomoConnectionTile${selectedId === connection.id ? " is-active" : ""}${drag?.id === connection.id ? " is-dragging" : ""}${drag && drag.id !== connection.id && drag.targetId === connection.id ? " is-drop-target" : ""}`}
        style={drag?.id === connection.id ? { transform: `translate(${drag.x}px, ${drag.y}px)` } : undefined}>
        <button type="button" className="mihomoConnectionSelect" aria-pressed={selectedId === connection.id} onClick={() => onSelect(connection.id)}>
          <span className={`protocol-${connection.component}`}><ProtocolIcon protocol={connection.component} /></span>
          <span><b>{connection.name}</b>{connection.route_state?.state === "stale" && <small title={connection.route_state.reason}>{connection.route_state.addresses.length ? `Stale · ${connection.route_state.addresses.join(", ")}` : connection.route_state.reason}</small>}
            <small>{modules.find((module) => module.id === connection.component)?.name} · {connection.settings.relay_host_id ? "Relay" : connection.settings.route_mode === "cdn" ? "CDN" : connection.settings.route_mode === "tls" ? "TLS" : "Прямое"}</small>
            <small>Приоритет {index + 1}</small></span>
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
            if (target) { move(connection.id, target.id); event.currentTarget.closest("[data-connection-id]")?.scrollIntoView({ block: "nearest", inline: "nearest" }); }
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
    <span className="mihomoConnectionOrderStatus" role="status">{announcement}</span>
  </div>;
}
