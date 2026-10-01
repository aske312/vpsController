"use client";

import { useEffect, useMemo, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { useFailureNotifications } from "../../shared/notifications/notification-center";
import { notificationFailure, type NotificationFailure } from "../../shared/notifications/store";
import type { MetricsHistory, MetricsPeriod } from "../../shared/types/control-plane";

export function useMetricsHistory(token: string, period: MetricsPeriod) {
  const api = useMemo(() => createApiClient(token), [token]);
  const [result, setResult] = useState<{ token: string; period: MetricsPeriod; data?: MetricsHistory; failure?: NotificationFailure }>();
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api<MetricsHistory>(`/metrics/history?period=${period}`);
        if (!cancelled) setResult({ token, period, data, failure: data.error ? { message: data.error } : undefined });
      } catch (error) {
        if (!cancelled) setResult((previous) => ({ token, period,
          data: previous?.token === token && previous.period === period ? previous.data : undefined,
          failure: notificationFailure(error, "История метрик недоступна"),
        }));
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), period === "live" ? 15000 : 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [api, token, period]);
  const current = result?.token === token && result.period === period ? result : undefined;
  const failures = useMemo(() => {
    const entries: Record<string, NotificationFailure> = {};
    if (current?.failure) entries.history = current.failure;
    return entries;
  }, [current]);
  useFailureNotifications("overview-metrics", "История метрик", failures);
  return { data: current?.data, stale: Boolean(current?.failure), pending: !current };
}
