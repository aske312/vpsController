import type { Module, Profile, ProfileStats, Status } from "../mihomo/types";

type SummaryData = {
  status: Status | null;
  modules: Module[] | null;
  profiles: Profile[] | null;
  profileStats: Record<string, ProfileStats["summary"]> | null;
};
type Section = keyof SummaryData;
export type MihomoSummary = SummaryData & { errors: Partial<Record<Section, string>> };
type ReadApi = <T>(path: string) => Promise<T>;

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
  const listeners = new Set<() => void>();
  const publish = (next: MihomoSummary) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const load = async <K extends Section>(section: K, read: () => Promise<SummaryData[K]>) => {
    try {
      const data = await read();
      const errors = { ...snapshot.errors };
      delete errors[section];
      publish({ ...snapshot, [section]: data, errors });
    } catch (cause) {
      const unauthorized = cause instanceof Error && "status" in cause && cause.status === 401;
      publish({ ...snapshot, errors: {
        ...snapshot.errors,
        [section]: unauthorized ? "Сессия панели завершена. Войдите заново." : sectionErrors[section],
      } });
    }
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
      pending = Promise.all([
        load("status", () => request<Status>("/mihomo/status")),
        load("modules", async () => items(await request<{ items: Module[] }>("/mihomo/modules"))),
        load("profiles", async () => items(await request<{ items: Profile[] }>("/mihomo/profiles"))),
        load("profileStats", async () => {
          const stats = items(await request<{ items: Array<ProfileStats & { id: string }> }>("/mihomo/stats"));
          return Object.fromEntries(stats.map((item) => [item.id, item.summary]));
        }),
      ]).then(() => { cachedAt = Date.now(); }).finally(() => { pending = null; });
      return pending;
    },
  };
}
