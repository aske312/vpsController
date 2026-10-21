"use client";

import { useEffect, useRef, useState } from "react";
import type { NetworkEndpointCheck } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";
import { networkEndpointSnapshot } from "./network-snapshot";

export type EndpointTarget = { id: string; owner: "direct" | "mihomo"; component: string; label: string; consumer_label: string; consumer_id: string | null; state: "pending" | "running" | "ready" | "failed" | "unavailable" | "unknown"; message: string };
export function endpointTargets(value: unknown, kind: NetworkEndpointCheck["kind"], domain: string) {
  const fail = () => { throw new Error("Некорректный список компонентов для проверки"); };
  if (!value || typeof value !== "object" || !("kind" in value) || value.kind !== kind || !("domain" in value) || value.domain !== domain || !("items" in value) || !Array.isArray(value.items) || !("observation" in value)) return fail();
  const seen = new Set<string>();
  for (const row of value.items) {
    if (!row || typeof row !== "object" || !["id", "component", "label", "consumer_label", "message"].every(key => typeof row[key] === "string") || seen.has(row.id)
      || !["direct", "mihomo"].includes(row.owner) || !["pending", "unavailable"].includes(row.state) || (row.consumer_id !== null && typeof row.consumer_id !== "string") || row.state === "pending" && !row.consumer_id) return fail();
    seen.add(row.id);
  }
  const observation = networkEndpointSnapshot(value.observation);
  if (observation.kind !== kind || observation.domain !== domain) return fail();
  return { items: value.items as EndpointTarget[], observation };
}

export function useEndpointSweep(request: NetworkRequest, onChecked?: () => void) {
  const [checking, setChecking] = useState(false);
  const [rows, setRows] = useState<EndpointTarget[]>([]);
  const [observation, setObservation] = useState<NetworkEndpointCheck | null>(null);
  const [scope, setScope] = useState("");
  const [failure, setFailure] = useState("");
  const active = useRef(true), running = useRef(false), stop = useRef(false);
  const { operation } = useNotifier("address-sweep", "Проверка адреса");
  useEffect(() => { active.current = true; return () => { active.current = false; stop.current = true; }; }, []);
  async function run(kind: NetworkEndpointCheck["kind"], domain: string) {
    if (running.current) return;
    running.current = true; stop.current = false; setChecking(true); setRows([]); setObservation(null); setFailure(""); setScope(`${kind}:${domain}`);
    const id = crypto.randomUUID();
    operation(id, "Проверка адреса", "running", "Определяем установленные компоненты Direct и Mihomo…");
    try {
      const snapshot = endpointTargets(await request(`/network/endpoints/check-targets?kind=${encodeURIComponent(kind)}&domain=${encodeURIComponent(domain)}`, { cache: "no-store" }), kind, domain);
      const next = snapshot.items.map(row => ({ ...row }));
      if (active.current) { setObservation(snapshot.observation); setRows([...next]); }
      for (const row of next) {
        if (stop.current) break;
        if (row.state !== "pending" || !row.consumer_id) continue;
        row.state = "running"; if (active.current) setRows(next.map(item => ({ ...item })));
        operation(id, "Проверка адреса", "running", `${row.owner === "direct" ? "Direct" : "Mihomo"} · ${row.label} · ${row.consumer_label}`);
        try {
          const result = await checkNetworkEndpoint(request, kind, domain, row.consumer_id);
          row.state = result.ready && result.verification === "end_to_end" ? "ready" : result.status === "warning" || result.status === "error" ? "failed" : "unavailable";
          row.message = result.message;
        } catch { row.state = "unknown"; row.message = "Результат не получен. Команда автоматически не повторяется."; }
        if (active.current) setRows(next.map(item => ({ ...item })));
      }
      for (const row of next) if (row.state === "pending") { row.state = "unavailable"; row.message = "Проверка остановлена"; }
      if (active.current) { setRows([...next]); onChecked?.(); }
      const passed = next.filter(row => row.state === "ready").length;
      operation(id, "Проверка адреса", next.length > 0 && passed === next.length ? "success" : "unknown", `Подтверждено ${passed} из ${next.length}. Остальные результаты не подтверждают доступность.`);
    } catch (cause) { const message = cause instanceof Error ? cause.message : "Не удалось начать проверку"; if (active.current) setFailure(message); operation(id, "Проверка адреса", "error", message); }
    finally { running.current = false; if (active.current) setChecking(false); }
  }
  return { checking, rows, observation, scope, failure, run, stop: () => { stop.current = true; } };
}
