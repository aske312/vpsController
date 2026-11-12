export type ProviderConnection = {
  state: "unchecked" | "checking" | "available" | "error";
  models: string[]; checkedAt: number | null; error: string;
};
export const initialProviderConnection: ProviderConnection = { state: "unchecked", models: [], checkedAt: null, error: "" };
type MonitorOptions = {
  fetch: (signal: AbortSignal) => Promise<{ models: string[] }>;
  publish: (value: ProviderConnection) => void;
  automatic: boolean; canPoll?: () => boolean;
  schedule?: typeof setTimeout; cancel?: typeof clearTimeout; now?: () => number;
};

/** Read-only discovery retries independently of chat. Never replay chat requests. */
export class ProviderMonitor {
  snapshot: ProviderConnection = { ...initialProviderConnection, models: [] };
  private stopped = false;
  private pending: Promise<void> | null = null;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private deadline: ReturnType<typeof setTimeout> | undefined;
  private options: MonitorOptions;
  constructor(options: MonitorOptions) { this.options = options; }
  private publish(value: ProviderConnection) { this.snapshot = value; this.options.publish(value); }
  private later(delay: number) {
    if (this.stopped || !this.options.automatic) return;
    (this.options.cancel ?? clearTimeout)(this.timer);
    this.timer = (this.options.schedule ?? setTimeout)(() => { void this.refresh(); }, delay);
  }
  resume() {
    if (this.snapshot.checkedAt === null || (this.options.now ?? Date.now)() - this.snapshot.checkedAt >= 5000) void this.refresh();
  }
  refresh(): Promise<void> {
    if (this.stopped) return Promise.resolve();
    if (this.pending) return this.pending;
    if (this.options.canPoll?.() === false) { this.later(15000); return Promise.resolve(); }
    (this.options.cancel ?? clearTimeout)(this.timer);
    const controller = this.controller = new AbortController();
    this.publish({ ...this.snapshot, state: "checking" });
    this.pending = this.run(controller);
    return this.pending;
  }
  private async run(controller: AbortController) {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([
        Promise.resolve().then(() => this.options.fetch(controller.signal)),
        new Promise<never>((_, reject) => {
          timeout = this.deadline = (this.options.schedule ?? setTimeout)(() => {
            controller.abort(); reject(new Error("Проверка API превысила время ожидания"));
          }, 20000);
        }),
      ]);
      if (this.stopped) return;
      if (!Array.isArray(result.models) || !result.models.length || result.models.some(id => typeof id !== "string" || !id || id.length > 128)) throw new Error("API вернул некорректный каталог моделей");
      this.publish({ state: "available", models: [...new Set(result.models)], checkedAt: (this.options.now ?? Date.now)(), error: "" });
      this.later(60000);
    } catch (cause) {
      if (this.stopped) return;
      this.publish({ ...this.snapshot, state: "error", checkedAt: (this.options.now ?? Date.now)(), error: cause instanceof Error ? cause.message : "Не удалось проверить API" });
      this.later(15000);
    } finally {
      (this.options.cancel ?? clearTimeout)(timeout);
      this.pending = null;
      this.controller = null;
    }
  }
  stop() {
    this.stopped = true;
    (this.options.cancel ?? clearTimeout)(this.timer);
    (this.options.cancel ?? clearTimeout)(this.deadline);
    this.controller?.abort();
  }
}

export function providerStatusText(value: ProviderConnection) {
  return ({ unchecked: "API ещё не проверен", checking: "Проверка API…", available: "Каталог API доступен", error: "Ошибка проверки API" })[value.state];
}
