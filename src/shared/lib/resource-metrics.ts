import type { ResourceHistory, ResourceMetrics } from "../types/control-plane";

export const knownMetric = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
export function usedBytes(total: number | null | undefined, available: number | null | undefined): number | null {
  return knownMetric(total) && total > 0 && knownMetric(available) && available <= total ? total - available : null;
}
export function usedPercent(total: number | null | undefined, available: number | null | undefined): number | null {
  const used = usedBytes(total, available);
  return used === null ? null : used / total! * 100;
}

export type NetworkSample = {rx: number; tx: number; at: number};
export function networkSample(resources: ResourceMetrics, previous: NetworkSample | null, now: number) {
  const {network_rx: rx, network_tx: tx} = resources;
  const sample = knownMetric(rx) && knownMetric(tx) ? {rx, tx, at: now} : null;
  const seconds = previous ? (now - previous.at) / 1000 : 0;
  const comparable = sample && previous && seconds > 0 && seconds <= 30 && sample.rx >= previous.rx && sample.tx >= previous.tx;
  return {
    sample,
    rate: {rx: comparable ? (sample.rx - previous.rx) / seconds : null, tx: comparable ? (sample.tx - previous.tx) / seconds : null},
  };
}

export function appendResources(history: ResourceHistory, resources: ResourceMetrics, rate: {rx: number | null; tx: number | null}, limit: number): ResourceHistory {
  const add = (values: (number | null)[], value: number | null) => [...values, knownMetric(value) ? value : null].slice(-limit);
  return {
    load: add(history.load, resources.cpu_percent),
    memory: add(history.memory, usedPercent(resources.memory_total, resources.memory_available)),
    disk: add(history.disk, usedPercent(resources.disk_total, resources.disk_available)),
    rx: add(history.rx, rate.rx), tx: add(history.tx, rate.tx),
  };
}

export function graphSegments(values: (number | null)[], maxValue: number): string[] {
  const data = values;
  const segments: string[][] = [];
  let segment: string[] = [];
  for (const [index, value] of data.entries()) {
    if (!knownMetric(value)) {
      if (segment.length) segments.push(segment);
      segment = [];
      continue;
    }
    const x = data.length === 1 ? 100 : index / (data.length - 1) * 100;
    const y = 50 - Math.min(50, value / Math.max(maxValue, 1) * 50);
    segment.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  }
  if (segment.length) segments.push(segment);
  return segments.map((points) => points.join(" "));
}
