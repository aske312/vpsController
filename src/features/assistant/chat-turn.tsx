/* eslint-disable @next/next/no-img-element */
"use client";

import { memo, useState, type ReactNode } from "react";
import { AssistantAnswer } from "./assistant-answer";
import { ChatIcon } from "./chat-icon";
import type { ChatFile } from "./chat-files";
import type { PendingAction } from "./confirmation-queue";
import type { ChatReplyStatus } from "./chat-jobs";
import { ChatActivity } from "./chat-activity";

export type ChatTurn = { stop_pending?: boolean; cancelled?: boolean; activity_stage?: string; activity_started_at?: number; queue_ahead?: number; status?: ChatReplyStatus; duration_ms?: number; command_ids?: string[]; system_event?: boolean; request_id?: string; queue_state?: "queued" | "running"; pending_action?: PendingAction; pending_actions?: PendingAction[]; action_result?: string; id: number; question: string; model: string; files: ChatFile[]; files_expired?: boolean; answer?: string; tools?: string[]; error?: string; context_safe?: boolean };

export const ChatTurnRow = memo(function ChatTurnRow({ turn, canRetry, onRetry, onCancel, onError, children, activityLabel }: {
  turn: ChatTurn; canRetry: boolean; children?: ReactNode; activityLabel?: string;
  onRetry: (turn: ChatTurn) => void; onCancel: (id: number) => void; onError: (message: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  return <div className="mcpTurn">
    <div className="mcpUserMessage">{turn.files.length > 0 && <div className="mcpFileList">{turn.files.map(file => <span className="mcpFileChip" key={file.id}>
      {file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name} loading="lazy" decoding="async"/> : <ChatIcon name="file"/>}<span>{file.name}</span>
    </span>)}</div>}{turn.question}{turn.files_expired && <small className="mcpExpiredFiles">Вложения удалены из кеша по лимиту</small>}</div>
    {(turn.answer || turn.error || children || activityLabel) && <article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>Nodera{turn.queue_state === "running" && turn.answer ? " · предварительный текст" : ""}</small>
      {(turn.status === "incomplete" || turn.status === "partial") && <p className="mcpFieldHint" role="status">Ответ получен не полностью</p>}
      <AssistantAnswer text={turn.error ? "⚠️ **Нужна проверка**\n\n" + turn.error : turn.answer || ""}/>
      {turn.tools?.length ? <p className="mcpToolList">{turn.tools.join(" · ")}</p> : null}
      {turn.error && <button type="button" className="ghostButton" disabled={!canRetry} onClick={() => onRetry(turn)}>Повторить</button>}
      {children}
      {activityLabel && <ChatActivity label={activityLabel} startedAt={turn.activity_started_at}/>}
    </article>}
    {turn.queue_state && <button type="button" className="ghostButton" disabled={turn.stop_pending || turn.activity_stage === "cancelling"} onClick={() => onCancel(turn.id)}>{turn.stop_pending || turn.activity_stage === "cancelling" ? "Останавливаем…" : turn.queue_state === "queued" ? "Отменить запрос" : "Остановить ИИ"}</button>}
    <div className="mcpAnswerActions">{turn.answer && !turn.queue_state && turn.duration_ms !== undefined && <small className="mcpResponseTime" title="Время с отправки запроса, включая ожидание в очереди">{turn.cancelled ? "Остановлено через" : "Ответ за"} {formatResponseTime(turn.duration_ms)}</small>}{turn.answer && !turn.error && <button type="button" className="mcpIconButton" aria-label="Скопировать ответ" title={copied ? "Скопировано" : "Скопировать ответ"} onClick={() => {
      void navigator.clipboard.writeText(turn.answer!).then(() => setCopied(true)).catch(() => onError("Не удалось скопировать ответ"));
    }}><ChatIcon name={copied ? "check" : "copy"}/></button>}</div>
  </div>;
});

function formatResponseTime(milliseconds: number) {
  const seconds = Math.max(1, Math.round(milliseconds / 1000));
  return seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`;
}
