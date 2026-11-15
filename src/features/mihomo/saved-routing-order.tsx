import { useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";

export function SavedRoutingOrder({ request, profileId, deviceId, disabled }: {
  request: ReturnType<typeof createApiClient>; profileId: string; deviceId: string; disabled: boolean;
}) {
  const [rules, setRules] = useState<string[]>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => pending.current?.abort(), []);
  const read = async () => {
    const controller = new AbortController();
    pending.current?.abort(); pending.current = controller;
    setBusy(true); setError("");
    try {
      const config = await request<string>(`/mihomo/profiles/${encodeURIComponent(profileId)}/config?device_id=${encodeURIComponent(deviceId)}`, { signal: controller.signal });
      const block = config.split("\nrules:\n")[1];
      if (!block) throw new Error("Итоговый порядок правил недоступен");
      const rows = block.split("\n").filter(line => line.startsWith("  - ")).map(line => JSON.parse(line.slice(4)) as string);
      if (!controller.signal.aborted) setRules(rows.slice(0, 2000));
    } catch (cause) { if (!controller.signal.aborted) setError(String(cause)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <details className="mihomoAdvancedRules"><summary>Итоговый порядок сохранённых правил</summary>
    <p>Включает системные, персональные, готовые и дополнительные правила в порядке применения к устройству.</p>
    <button type="button" disabled={disabled || busy} onClick={() => void read()}>{busy ? "Чтение…" : "Показать итоговый порядок"}</button>
    {disabled && <small>Сохраните изменения профиля для просмотра актуального результата.</small>}
    {error && <p role="alert">{error}</p>}{!disabled && rules && <ol className="mihomoFinalRuleOrder">{rules.map((rule, index) => <li key={`${index}:${rule}`}><code>{rule}</code></li>)}</ol>}
  </details>;
}
