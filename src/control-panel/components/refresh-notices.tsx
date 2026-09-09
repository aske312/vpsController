"use client";

import { useFailureNotifications } from "../../shared/notifications/notification-center";

export type RefreshFailure = { message: string; network: boolean };
export { notificationFailure as refreshFailure } from "../../shared/notifications/store";

export function RefreshNotices({ errors, reconnecting }: { errors: Record<string, RefreshFailure>; reconnecting: boolean }) {
  useFailureNotifications("panel", "Обновление данных панели", errors, reconnecting);
  return null;
}
