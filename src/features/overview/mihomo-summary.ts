import type { Module, Profile, ProfileStats, Status } from "../mihomo/types";
import type { ApiRequestInit } from "../../shared/lib/api-request";

type SummaryData = {
  status: Status | null;
  modules: Module[] | null;
  profiles: Profile[] | null;
  profileStats: Record<string, ProfileStats["summary"]> | null;
};
type Section = keyof SummaryData;
export type MihomoSummary = SummaryData & { errors: Partial<Record<Section, string>>; networkErrors?: Partial<Record<Section, boolean>> };
type ReadApi = <T>(path: string, init?: ApiRequestInit<T>) => Promise<T>;

export const EMPTY_MIHOMO_SUMMARY: MihomoSummary = {
  status: null, modules: null, profiles: null, profileStats: null, errors: {},
};
const CACHE_TTL_MS = 15_000;
const sectionErrors: Record<Section, string> = {
  status: "Не удалось обновить состояние Mihomo",
  modules: "Не удалось обновить каналы Mihomo",
  profiles: "Не удалось обновить профили Mihomo",
  profileStats: "Не удалось обновить трафик Mihomo",
};

function items<T>(data: { items: T[] }): T[] {
  if (!Array.isArray(data?.items)) throw new Error("Invalid list response");
  return data.items;
}

// Each response updates its own section: slow or failed traffic must not hide profiles.
export function createMihomoSummaryStore(request: ReadApi) {
  let snapshot = EMPTY_MIHOMO_SUMMARY;
  let cachedAt = 0;
  let pending: Promise<void> | null = null;
  let generation = 0;
  const listeners = new Set<() => void>();
  const publish = (next: MihomoSummary) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const load = async <K extends Section>(section: K, path: string, decode: (value: unknown) => SummaryData[K], force: boolean, version: number) => {
    const accept = (value: unknown) => {
      if (version !== generation) return;
      const data = decode(value);
      const errors = { ...snapshot.errors };
      const networkErrors = { ...snapshot.networkErrors };
      delete errors[section];
      delete networkErrors[section];
      publish({ ...snapshot, [section]: data, errors, networkErrors });
    };
    const fail = (cause: unknown) => {
      if (version !== generation) return;
      const unauthorized = cause instanceof Error && "status" in cause && cause.status === 401;
      publish({ ...snapshot, networkErrors: { ...snapshot.networkErrors, [section]: cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response") }, errors: {
        ...snapshot.errors,
        [section]: unauthorized ? "Сессия панели завершена. Войдите заново." : sectionErrors[section],
      } });
    };
    try {
      accept(await request(path, { ...(force ? { cache: "no-store" } : {}), onRevalidated: accept, onRevalidationError: fail }));
    } catch (cause) { fail(cause); }
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    refresh(force = false): Promise<void> {
      if (pending) return pending;
      if (!force && cachedAt && Date.now() - cachedAt < CACHE_TTL_MS) return Promise.resolve();
      const version = ++generation;
      pending = Promise.all([
        load("status", "/mihomo/status", (value) => value as Status, force, version),
        load("modules", "/mihomo/modules", (value) => items(value as { items: Module[] }), force, version),
        load("profiles", "/mihomo/profiles", (value) => items(value as { items: Profile[] }), force, version),
        load("profileStats", "/mihomo/stats", (value) => {
          const stats = items(value as { items: Array<ProfileStats & { id: string }> });
          return Object.fromEntries(stats.map((item) => [item.id, item.summary]));
        }, force, version),
      ]).then(() => { cachedAt = Date.now(); }).finally(() => { pending = null; });
      return pending;
    },
  };
}
