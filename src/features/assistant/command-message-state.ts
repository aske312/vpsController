type OperationLink = { operation_id?: string };
type MessageLinks = { id: number; command_ids?: string[]; pending_action?: OperationLink; pending_actions?: OperationLink[] };
type Scope = { sessionKey?: string };
const validId = (id: unknown): id is string => typeof id === "string" && /^[a-f0-9]{32}$/.test(id);

export function messageCommandIds(turn: MessageLinks): string[] {
  return [...new Set([...(Array.isArray(turn.command_ids) ? turn.command_ids : []), ...(turn.pending_actions || []).map(action => action.operation_id), turn.pending_action?.operation_id].filter(validId))].slice(0, 64);
}

export function commandMessageOwner(turns: MessageLinks[], ids: string[]) {
  return turns.find(turn => messageCommandIds(turn).some(id => ids.includes(id)))?.id;
}

const storageKey = (scope: Scope) => scope.sessionKey ? `nodera-session:${scope.sessionKey}:closed-operations` : undefined;
export function closedCommandMessages(scope: Scope): Set<string> {
  const key = storageKey(scope);
  try {
    const value: unknown = key && JSON.parse(sessionStorage.getItem(key) || "[]");
    return new Set(Array.isArray(value) ? value.filter(validId).slice(-200) : []);
  } catch { return new Set(); }
}

export function rememberClosedCommand(scope: Scope, id: string) {
  const key = storageKey(scope);
  if (!key || !validId(id)) return;
  const closed = closedCommandMessages(scope);
  closed.delete(id); closed.add(id);
  try { sessionStorage.setItem(key, JSON.stringify([...closed].slice(-200))); } catch { /* In-memory dismissal remains available. */ }
}
