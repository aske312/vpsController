export class OperationFailedError extends Error {}

export function operationCompleted(action: { unit?: string; state?: string; result?: string; message?: string } | undefined, expectedUnit: string): boolean {
  if (!action || (expectedUnit && action.unit !== expectedUnit)) return false;
  if (action.state === "failed" || (action.result && !["success", "unknown"].includes(action.result))) {
    throw new OperationFailedError(action.message || "Серверная операция завершилась с ошибкой");
  }
  return ["succeeded", "finished"].includes(action.state || "") && action.result !== "unknown";
}
