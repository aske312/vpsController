import type { ProfileConnection } from "./types";

/** Move within one device's slots, preserving other devices and connection data. */
export function reorderDeviceConnections(connections: ProfileConnection[], sourceId: string, targetId: string): ProfileConnection[] {
  const source = connections.find((item) => item.id === sourceId);
  const target = connections.find((item) => item.id === targetId);
  if (!source || !target || source === target || source.device_id !== target.device_id) return connections;
  const ordered = connections.filter((item) => item.device_id === source.device_id);
  const targetIndex = ordered.indexOf(target);
  ordered.splice(ordered.indexOf(source), 1);
  ordered.splice(targetIndex, 0, source);
  let index = 0;
  return connections.map((item) => item.device_id === source.device_id ? ordered[index++] : item);
}
