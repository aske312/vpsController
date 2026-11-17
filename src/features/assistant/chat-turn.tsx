/* eslint-disable @next/next/no-img-element */
"use client";

import { memo, useState } from "react";
import { AssistantAnswer } from "./assistant-answer";
import { ChatIcon } from "./chat-icon";
import type { ChatFile } from "./chat-files";
import type { PendingAction } from "./confirmation-queue";

export type ChatTurn = { request_id?: string; queue_state?: "queued" | "running"; pending_action?: PendingAction; pending_actions?: PendingAction[]; action_result?: string; id: number; question: string; model: string; files: ChatFile[]; files_expired?: boolean; answer?: string; tools?: string[]; error?: string; context_safe?: boolean };

export const ChatTurnRow = memo(function ChatTurnRow({ turn, waiting, canRetry, onRetry, onCancel, onError, onTrace }: {
  turn: ChatTurn; waiting: string; canRetry: boolean;
  onRetry: (turn: ChatTurn) => void; onCancel: (id: number) => void; onError: (message: string) => void;
  onTrace: (id: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  return <div className="mcpTurn">
    <div className="mcpUserMessage">{turn.files.length > 0 && <div className="mcpFileList">{turn.files.map(file => <span className="mcpFileChip" key={file.id}>
      {file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name} loading="lazy" decoding="async"/> : <ChatIcon name="file"/>}<span>{file.name}</span>
    </span>)}</div>}{turn.question}{turn.files_expired && <small className="mcpExpiredFiles">Вложения удалены из кеша по лимиту</small>}</div>
    <article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>Nodera</small>
      {turn.error || turn.answer ? <AssistantAnswer text={turn.error ? "⚠️ **Нужна проверка**\n\n" + turn.error : turn.answer || ""}/> : <div className="mcpThinking" role="status"><span className="mcpThinkingDots" aria-hidden="true"><i/><i/><i/></span><span>{waiting}</span></div>}
      {turn.queue_state === "queued" && <button type="button" className="ghostButton" onClick={() => onCancel(turn.id)}>Убрать из очереди</button>}
      {turn.tools?.length ? <p className="mcpToolList">{turn.tools.join(" · ")}</p> : null}
      {turn.error && <button type="button" className="ghostButton" disabled={!canRetry} onClick={() => onRetry(turn)}>Повторить</button>}
    </article>
    {turn.action_result && <div className="mcpActionResult" role="status"><AssistantAnswer text={turn.action_result}/></div>}
    <div className="mcpAnswerActions">{turn.answer && <button type="button" className="mcpIconButton" aria-label="Скопировать ответ" title={copied ? "Скопировано" : "Скопировать ответ"} onClick={() => {
      void navigator.clipboard.writeText(turn.answer!).then(() => setCopied(true)).catch(() => onError("Не удалось скопировать ответ"));
    }}><ChatIcon name={copied ? "check" : "copy"}/></button>}{turn.request_id && <button type="button" className="ghostButton" title={`Журнал запроса ${turn.request_id}`} onClick={() => onTrace(turn.request_id!)}>Журнал запроса</button>}</div>
  </div>;
});
