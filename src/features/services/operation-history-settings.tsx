"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { refreshedHistoryDraft, type HistoryPolicy as Policy } from "./history-settings-state";

type Confirmation = (options: { title: string; message: string; confirmLabel: string; danger: boolean }) => Promise<boolean>;
type Client = ReturnType<typeof createApiClient>;
const endpoint = "/services/operation-history";

export function useOperationHistorySettings(request: Client, enabled: boolean) {
  const [state, setState] = useState<{ client: Client; saved?: Policy; draft?: Policy; error?: string; saving?: boolean }>();
  const currentClient = useRef(request);
  const readingGeneration = useRef(0);
  const saving = useRef(false);
  useEffect(() => { currentClient.current = request; }, [request]);
  const load = useCallback(async () => {
    if (saving.current) return;
    const generation = ++readingGeneration.current;
    try {
      const saved = await request<Policy>(endpoint);
      if (currentClient.current !== request || generation !== readingGeneration.current) return;
      setState((previous) => ({ client: request, saved,
        draft: previous?.client === request ? refreshedHistoryDraft(previous.saved, previous.draft, saved) : saved,
      }));
    } catch (error) {
      if (currentClient.current !== request || generation !== readingGeneration.current) return;
      setState((previous) => ({ ...(previous?.client === request ? previous : {}), client: request,
        error: error instanceof Error ? error.message : "История операций недоступна",
      }));
    }
  }, [request]);
  useEffect(() => { if (enabled) void load(); }, [enabled, load]);
  const current = state?.client === request ? state : undefined;
  const save = async (confirm: Confirmation) => {
    if (!enabled || !current?.saved || !current.draft || current.saving || saving.current || current.saved.revision !== current.draft.revision) return;
    const { saved, draft } = current;
    saving.current = true;
    readingGeneration.current++;
    setState({ ...current, saving: true, error: undefined });
    try {
      if ((draft.retention_days < saved.retention_days || draft.disk_limit_mb < saved.disk_limit_mb || draft.log_retention_days < saved.log_retention_days || draft.log_disk_limit_mb < saved.log_disk_limit_mb) && !await confirm({
        title: "Сократить историю операций?", message: "При следующей очистке старые завершённые операции могут быть удалены. Активные операции и операции с неизвестным результатом сохраняются.", confirmLabel: "Сохранить лимиты", danger: true,
      })) return;
      if (currentClient.current !== request) return;
      const result = await request<Policy>(endpoint, { method: "PUT", body: JSON.stringify({
        retention_days: draft.retention_days, disk_limit_mb: draft.disk_limit_mb, expected_revision: draft.revision,
        log_retention_days: draft.log_retention_days, log_disk_limit_mb: draft.log_disk_limit_mb,
      }) });
      if (currentClient.current === request) setState({ client: request, saved: result, draft: result });
    } catch (error) {
      if (currentClient.current !== request) return;
      setState({ ...current, saving: true, error: error instanceof Error ? error.message : "Не удалось сохранить настройки" });
      // Reconcile by reading only. Never repeat a write after a lost response.
      try {
        const actual = await request<Policy>(endpoint);
        if (currentClient.current === request) setState((previous) => ({ ...previous!, saved: actual }));
      } catch { /* Keep both the draft and the original failure. */ }
    } finally {
      saving.current = false;
      if (currentClient.current === request) setState((previous) => previous?.client === request ? { ...previous, saving: false } : previous);
    }
  };
  return {
    ...current,
    load,
    save,
    change: (patch: Partial<Pick<Policy, "retention_days" | "disk_limit_mb" | "log_retention_days" | "log_disk_limit_mb">>) => { if (!saving.current) setState((previous) => previous?.client === request && previous.draft ? { ...previous, draft: { ...previous.draft, ...patch } } : previous); },
    reset: () => { if (!saving.current) setState((previous) => previous?.client === request ? { ...previous, draft: previous.saved, error: undefined } : previous); },
  };
}

export function OperationHistorySettings({ policy, confirm, busy }: {
  policy: ReturnType<typeof useOperationHistorySettings>; confirm: Confirmation; busy: boolean;
}) {
  const { saved, draft, error, saving } = policy;
  const conflict = Boolean(saved && draft && saved.revision !== draft.revision);
  const valid = draft && [draft.retention_days, draft.log_retention_days].every((value) => Number.isInteger(value) && value >= 1 && value <= 730) && [draft.disk_limit_mb, draft.log_disk_limit_mb].every((value) => Number.isInteger(value) && value >= 1 && value <= 1024);
  return <section className="servicesLog servicesHistory" aria-label="Хранение истории операций">
    <div className="operationsTitle"><p className="eyebrow">OPERATION HISTORY</p><h3>История операций</h3></div>
    <div className="operationsBody">
    {error && <p role="alert">{error}</p>}
    {conflict && <p role="status">Настройки на сервере изменились. Ваш ввод сохранён; отмените правки, чтобы принять актуальные значения.</p>}
    <div className="servicesHistoryFields">
      <label className="opsField"><span>Хранить, дней</span><input type="number" min={1} max={730} value={draft?.retention_days ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ retention_days: Number(event.target.value) })} /></label>
      <label className="opsField"><span>На каждый архив, МиБ</span><input type="number" min={1} max={1024} value={draft?.disk_limit_mb ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ disk_limit_mb: Number(event.target.value) })} /></label>
      <label className="opsField"><span>Журналы этапов, дней</span><input type="number" min={1} max={730} value={draft?.log_retention_days ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ log_retention_days: Number(event.target.value) })} /></label>
      <label className="opsField"><span>Все журналы этапов, МиБ</span><input type="number" min={1} max={1024} value={draft?.log_disk_limit_mb ?? ""} disabled={!draft || busy || saving} onChange={(event) => policy.change({ log_disk_limit_mb: Number(event.target.value) })} /></label>
    </div>
    {saved?.logs && <p>Этапы: {(saved.logs.used_bytes / 1024 / 1024).toFixed(1)} МиБ{saved.logs.over_limit ? " · превышение лимита" : ""}</p>}
    {saved && <p>{Object.entries(saved.sources).map(([name, source]) => `${({ system: "Система", mihomo: "Mihomo", cdn: "CDN" } as Record<string, string>)[name] ?? name}: ${(source.used_bytes / 1024 / 1024).toFixed(1)} МиБ${source.over_limit ? " — лимит превышен" : ""}`).join("; ")}</p>}
    <small className="servicesSettingsHint">Активные и неизвестные операции сохраняются. Лимиты применятся при очистке.</small>
    </div>
    <div className="servicesLogActions">
      <button type="button" className="servicePrimary" disabled={busy || saving || !valid || conflict} onClick={() => void policy.save(confirm)}>{saving ? "Сохранение…" : "Сохранить"}</button>
      <button type="button" className="servicePrimary ghost" disabled={saving || !saved} onClick={policy.reset}>Сбросить</button>
      <button type="button" className="servicePrimary ghost" disabled={saving} onClick={() => void policy.load()}>Обновить</button>
    </div>
  </section>;
}
