"use client";

import { useEffect, useState } from "react";
import type { NetworkRequest } from "./network-api";
import { resourceBindingsSnapshot, type ResourceBinding } from "./resource-usage";

export function useResourceUsage(request: NetworkRequest, refreshKey: number, snapshotKey: string | undefined, busy: boolean) {
  const [result, setResult] = useState<{ bindings: ResourceBinding[]; request: NetworkRequest } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (busy || !snapshotKey) return;
    const controller = new AbortController();
    void request("/network/routes", { cache: "no-store", signal: controller.signal }).then(resourceBindingsSnapshot).then(bindings => {
      if (!controller.signal.aborted) { setResult({ bindings, request }); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [request, refreshKey, snapshotKey, busy]);
  return { bindings: !busy && !failed && result?.request === request ? result.bindings : null, failed };
}
