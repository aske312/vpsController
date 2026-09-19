"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";

type Policy = {
  retention_days: number;
  disk_limit_mb: number;
  revision: string;
  sources: Record<string, { used_bytes: number; over_limit: boolean }>;
};
type Confirmation = (options: { title: string; message: string; confirmLabel: string; danger: boolean }) => Promise<boolean>;
type Client = ReturnType<typeof createApiClient>;
const endpoint = "/services/operation-history";

export function useOperationHistorySettings(request: Client, enabled: boolean) {
  const [state, setState] = useState<{ client: Client; saved?: Policy; draft?: Policy; error?: string; saving?: boolean }>();
  const currentClient = useRef(request);
  useEffect(() => { currentClient.current = request; }, [request]);
  const load = useCallback(async () => {
    try {
      const saved = await request<Policy>(endpoint);
      if (currentClient.current !== request) return;
      setState((previous) => ({ client: request, saved,
        draft: previous?.client === request ? previous.draft ?? saved : saved,
      }));
    } catch (error) {
      if (currentClient.current !== request) return;
      setState((previous) => ({ ...(previous?.client === request ? previous : {}), client: request,
        error: error instanceof Error ? error.message : "История операций недоступна",
      }));
    }
  }, [request]);
  useEffect(() => { if (enabled) void load(); }, [enabled, load]);
  const current = state?.client === request ? state : undefined;
  const save = async (confirm: Confirmation) => {
    if (!current?.saved || !current.draft || current.saving) return;
    const { saved, draft } = current;
    if ((draft.retention_days < saved.retention_days || draft.disk_limit_mb < saved.disk_limit_mb) && !await confirm({
      title: "Сократить историю операций?", message: "При следующей очистке старые завершённые операции могут быть удалены. Активные операции и операции с неизвестным результатом сохраняются.", confirmLabel: "Сохранить лимиты", danger: true,
    })) return;
    if (currentClient.current !== request) return;
    setState({ ...current, saving: true, error: undefined });
    try {
      const result = await request<Policy>(endpoint, { method: "PUT", body: JSON.stringify({
        retention_days: draft.retention_days, disk_limit_mb: draft.disk_limit_mb, expected_revision: draft.revision,
      }) });
      if (currentClient.current === request) setState({ client: request, saved: result, draft: result });
    } catch (error) {
      if (currentClient.current !== request) return;
      setState({ ...current, saving: false, error: error instanceof Error ? error.message : "Не удалось сохранить настройки" });
      // Reconcile by reading only. Never repeat a write after a lost response.
      try {
        const actual = await request<Policy>(endpoint);
        if (currentClient.current === request) setState((previous) => ({ ...previous!, saved: actual }));
      } catch { /* Keep both the draft and the original failure. */ }
    }
  };
  return {
    ...current,
    load,
    save,
    change: (patch: Partial<Pick<Policy, "retention_days" | "disk_limit_mb">>) => setState((previous) => previous?.client === request && previous.draft ? { ...previous, draft: { ...previous.draft, ...patch } } : previous),
    reset: () => setState((previous) => previous?.client === request ? { ...previous, draft: previous.saved, error: undefined } : previous),
  };
}

export function OperationHistorySettings({ policy, confirm, busy }: {
  policy: ReturnType<typeof useOperationHistorySettings>; confirm: Confirmation; busy: boolean;
}) {
  const { saved, draft, error, saving } = policy;
  const conflict = Boolean(saved && draft && saved.revision !== draft.revision);
  const valid = draft && Number.isInteger(draft.retention_days) && draft.retention_days >= 1 && draft.retention_days <= 730 && Number.isInteger(draft.disk_limit_mb) && draft.disk_limit_mb >= 1 && draft.disk_limit_mb <= 1024;
  return <section className="servicesLog" aria-label="Хранение истории операций">
    <h3>История операций</h3>
    <p>Срок хранения завершённых операций и лимит каждого архива: системы, Mihomo и CDN.</p>
    {error && <p role="alert">{error}</p>}
    {conflict && <p role="status">Настройки на сервере изменились. Ваш ввод сохранён; отмените правки, чтобы принять актуальные значения.</p>}
    <div className="opsFields">
      <label className="opsField"><span>Хранить, дней</span><input type="number" min={1} max={730} value={draft?.retention_days ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ retention_days: Number(event.target.value) })} /></label>
      <label className="opsField"><span>На каждый архив, МиБ</span><input type="number" min={1} max={1024} value={draft?.disk_limit_mb ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ disk_limit_mb: Number(event.target.value) })} /></label>
    </div>
    {saved && <p>{Object.entries(saved.sources).map(([name, source]) => `${({ system: "Система", mihomo: "Mihomo", cdn: "CDN" } as Record<string, string>)[name] ?? name}: ${(source.used_bytes / 1024 / 1024).toFixed(1)} МиБ${source.over_limit ? " — лимит превышен" : ""}`).join("; ")}</p>}
    <p>Лимиты применяются при следующей очистке истории. Активные, неизвестные операции и записи с нужными временными файлами защищены; поэтому архив может превышать лимит. Подробные журналы настраиваются отдельно.</p>
    <div className="opsActions">
      <button type="button" className="servicePrimary" disabled={busy || saving || !valid || conflict} onClick={() => void policy.save(confirm)}>{saving ? "Сохранение…" : "Сохранить"}</button>
      <button type="button" disabled={saving || !saved} onClick={policy.reset}>Отменить правки</button>
      <button type="button" disabled={saving} onClick={() => void policy.load()}>Обновить данные</button>
    </div>
  </section>;
}
