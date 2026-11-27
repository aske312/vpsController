"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { NotificationNotice, useNotifier, usePageError } from "../../shared/notifications/notification-center";
import { SettingsDialog } from "./nodera-settings";
import { ChatIcon } from "./chat-icon";
import "./telegram-campaigns.css";

type Question = { kind: "choice" | "text"; text: string; options: string[]; multiple: boolean };
type Audience = { mode: "all" | "subscribers" | "selected"; ids: number[] };
type Draft = { id: string; revision: number; kind: "message" | "poll" | "survey"; title: string; text: string; audience: Audience; questions: Question[] };
type Recipient = { telegram_id: number; name: string; role: string; subscriber: boolean; eligible: boolean; excluded_reason: string };
type Counts = Partial<Record<"pending" | "sending" | "sent" | "failed" | "unknown" | "cancelled" | "skipped", number>>;
type Campaign = Draft & { state: "draft" | "active" | "completed" | "closed"; created_at: number; updated_at: number;
  recipient_count: number; delivery_counts: Counts; step_counts: Counts; pending_steps: number; unconfirmed_closes: number;
  respondents: number; completed_responses: number; answer_total: number; answers_offset?: number;
  results: { answered: number; counts: number[] }[];
  answers?: { telegram_id: number; step: number; answer: string | number[]; at: number }[];
  deliveries?: { recipient: number; step: number; state: string; close_state: string }[] };
type Overview = { ready: boolean; minimum_bot_version: string; recipients: Recipient[]; items: Campaign[] };
type Preview = { campaign: Campaign; confirmation: string; expires_at: number; recipients: Recipient[]; excluded: Recipient[] };
type Props = { request: ReturnType<typeof createApiClient>; active: boolean; blocked: boolean;
  onDirtyChange: (dirty: boolean) => void; onUpdate: () => Promise<void> };

const kindNames = { message: "Уведомление", poll: "Голосование", survey: "Анкета" };
const stateNames = { draft: "Черновик", active: "Запущена", completed: "Отправка завершена", closed: "Завершена владельцем" };
const deliveryNames: Record<string, string> = { pending: "В очереди", sending: "Отправляется", sent: "Отправлено", failed: "Ошибка отправки",
  unknown: "Результат неизвестен", cancelled: "Отменено", skipped: "Получатель недоступен" };
const excludedNames: Record<string, string> = { blocked: "Заблокирован", muted: "Отключил рассылки", not_started: "Не запускал бота" };
const newQuestion = (): Question => ({ kind: "choice", text: "", options: ["", ""], multiple: false });
const editable = (value: Draft): Draft => ({ id: value.id, revision: value.revision, kind: value.kind, title: value.title, text: value.text,
  audience: value.audience, questions: value.questions });
const date = (value: number) => new Date(value * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });

export function TelegramCampaigns({ request, active, blocked, onDirtyChange, onUpdate }: Props) {
  const notify = useNotifier("nodera-campaigns", "Рассылки Telegram");
  const [overview, setOverview] = useState<Overview>();
  const [selection, setSelection] = useState<string>();
  const [detail, setDetail] = useState<Campaign>();
  const [editor, setEditor] = useState<Draft>();
  const [baseline, setBaseline] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [confirm, setConfirm] = useState<"close" | "delete">();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const writing = useRef(false);
  const dirty = Boolean(editor && JSON.stringify(editor) !== baseline);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  usePageError("nodera-campaigns", "Рассылки Telegram", active ? error : "");
  const load = useCallback(async () => {
    const result = await request<Overview>("/assistant/campaigns", { cache: "no-store" });
    setOverview(result);
  }, [request]);
  useEffect(() => {
    if (!active) return;
    let current = true;
    void request<Overview>("/assistant/campaigns", { cache: "no-store" }).then(value => { if (current) { setOverview(value); setError(""); } },
      cause => { if (current) setError(cause instanceof Error ? cause.message : "Рассылки недоступны"); });
    return () => { current = false; };
  }, [active, request]);
  useEffect(() => {
    if (!active || !selection || editor) return;
    let current = true;
    const refresh = () => { void request<Campaign>(`/assistant/campaigns/${selection}?offset=${offset}`, { cache: "no-store" }).then(
      value => { if (current) { setDetail(value); setError(""); setOverview(old => old && ({ ...old, items: old.items.map(item => item.id === value.id ? value : item) })); } },
      cause => { if (current) setError(cause instanceof Error ? cause.message : "Результаты недоступны"); }); };
    refresh(); const timer = setInterval(refresh, 5000);
    return () => { current = false; clearInterval(timer); };
  }, [active, selection, offset, editor, request]);

  async function perform(action: () => Promise<void>) {
    if (writing.current || blocked) return;
    writing.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Действие не выполнено"); }
    finally { writing.current = false; setBusy(false); }
  }
  function begin(value?: Campaign) {
    const draft: Draft = value ? editable(value) : { id: crypto.randomUUID().replaceAll("-", ""), revision: 0,
      kind: "message", title: "", text: "", audience: { mode: "all", ids: [] }, questions: [] };
    setEditor(draft); setBaseline(JSON.stringify(draft)); setPreview(undefined); setOffset(0); setSearch("");
  }
  async function saveDraft() {
    if (!editor) return;
    const saved = await request<Campaign>("/assistant/campaigns", { method: "POST", body: JSON.stringify(editor) });
    const next = editable(saved); setEditor(next); setBaseline(JSON.stringify(next)); setDetail(saved); setSelection(saved.id);
    await load(); return saved;
  }
  async function review() {
    const saved = await saveDraft();
    if (saved) setPreview(await request<Preview>(`/assistant/campaigns/${saved.id}/preview`, { method: "POST", body: JSON.stringify({ revision: saved.revision }) }));
  }
  async function send() {
    if (!preview) return;
    const saved = await request<Campaign>(`/assistant/campaigns/${preview.campaign.id}/send`, { method: "POST",
      body: JSON.stringify({ revision: preview.campaign.revision, confirmation: preview.confirmation }) });
    setPreview(undefined); setEditor(undefined); setBaseline(""); setDetail(saved); setSelection(saved.id);
    notify.success("Рассылка поставлена в очередь"); await load();
  }
  function questionPatch(index: number, patch: Partial<Question>) {
    setEditor(old => old && ({ ...old, questions: old.questions.map((question, i) => i === index ? { ...question, ...patch } : question) }));
  }
  function changeKind(kind: Draft["kind"]) {
    setEditor(old => old && ({ ...old, kind, questions: kind === "message" ? [] : kind === "poll" ? [{ ...(old.questions[0] ?? newQuestion()), kind: "choice", options: old.questions[0]?.kind === "choice" ? old.questions[0].options : ["", ""] }] : old.questions.length ? old.questions : [newQuestion()] }));
  }
  const locked = busy || blocked;
  const names = new Map(overview?.recipients.map(item => [item.telegram_id, item.name]));
  const selectedIds = new Set(editor?.audience.ids ?? []);
  const visibleRecipients = (overview?.recipients ?? []).filter(item => `${item.name} ${item.telegram_id}`.toLowerCase().includes(search.toLowerCase()));
  const chosenCount = overview?.recipients.filter(item => item.eligible && (editor?.audience.mode === "all" || editor?.audience.mode === "subscribers" && item.subscriber || selectedIds.has(item.telegram_id))).length ?? 0;

  return <section className="campaignWorkspace" hidden={!active} aria-label="Рассылки владельца">
    <div className="campaignToolbar"><span className="campaignAudienceCount">{overview ? `${overview.recipients.filter(item => item.eligible).length} получателей · Telegram` : "Telegram"}</span>
      <button type="button" className="primaryButton" disabled={locked || Boolean(editor)} onClick={() => begin()}><ChatIcon name="plus"/>Создать рассылку</button></div>
    {!overview && !error && <p role="status">Загружаем рассылки…</p>}
    {overview && !overview.ready && <div className="campaignSetup"><strong>Подготовьте Telegram-бота</strong><p>Для отправки и сбора ответов включите NoderaBot и обновите Nodera. Черновики можно создавать сейчас.</p>
      <button type="button" className="ghostButton" disabled={locked} onClick={() => void onUpdate()}>Обновить Nodera</button></div>}
    <div className="campaignColumns">
      <aside className="campaignHistory" aria-label="История рассылок">
        <h3>Рассылки <span>{overview?.items.length ?? 0}</span></h3>
        {overview?.items.length === 0 && <p>Пока нет рассылок. Создайте уведомление или задайте пользователям вопрос.</p>}
        {overview?.items.map(item => <button type="button" key={item.id} className="campaignHistoryItem" aria-pressed={selection === item.id}
          disabled={locked || Boolean(editor)} onClick={() => { setSelection(item.id); setDetail(undefined); setOffset(0); }}>
          <strong>{item.title}</strong><span>{kindNames[item.kind]} · {stateNames[item.state]}</span>
          <small>{date(item.created_at)}{item.state !== "draft" && ` · ${item.respondents}/${item.recipient_count} ответили`}</small></button>)}
      </aside>
      <div className="campaignMain">
        {editor ? <form className="campaignEditor" onInvalidCapture={event => { (event.target as HTMLElement).closest("details")?.setAttribute("open", ""); }} onSubmit={event => { event.preventDefault(); void perform(async () => { await saveDraft(); notify.success("Черновик сохранён"); }); }}>
          <div className="campaignSectionHeading"><h3>{editor.revision ? "Черновик" : "Новая рассылка"}</h3><span>{editor.revision ? "Сохранён" : "Не отправлена"}</span></div>
          <fieldset disabled={locked}>
            <div className="campaignFormRow"><label>Формат<select value={editor.kind} onChange={event => changeKind(event.target.value as Draft["kind"])}>{Object.entries(kindNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
            <label>Название<input required maxLength={120} value={editor.title} placeholder="Например, плановые работы" onChange={event => setEditor({ ...editor, title: event.target.value })}/></label></div>
            <label>{editor.kind === "message" ? "Текст уведомления" : "Вступление"}<textarea rows={3} required={editor.kind === "message"} maxLength={3000} value={editor.text}
              placeholder={editor.kind === "message" ? "Что пользователям нужно знать?" : "Расскажите, зачем нужен опрос и сколько времени он займёт."} onChange={event => setEditor({ ...editor, text: event.target.value })}/></label>
            <div className="campaignSectionHeading"><h4>Получатели</h4><span>{chosenCount} доступно</span></div>
            <label>Аудитория<select value={editor.audience.mode} onChange={event => setEditor({ ...editor, audience: { mode: event.target.value as Audience["mode"], ids: [] } })}>
              <option value="all">Все пользователи бота</option><option value="subscribers">С привязанной подпиской</option><option value="selected">Выбранные пользователи</option></select></label>
            <details className="campaignHelp"><summary>Кто получит рассылку</summary><p>Только пользователи, которые запускали этого бота и не отключили рассылки. Заблокированные исключаются.</p></details>
            {editor.audience.mode === "selected" && <div className="campaignRecipients"><label>Поиск получателя<input value={search} onChange={event => setSearch(event.target.value)} placeholder="Имя или Telegram ID"/></label>
              <div className="campaignRecipientList">{visibleRecipients.slice(0,100).map(item => <label key={item.telegram_id}><input type="checkbox" disabled={!item.eligible} checked={selectedIds.has(item.telegram_id)}
                onChange={event => setEditor({ ...editor, audience: { mode: "selected", ids: event.target.checked ? [...editor.audience.ids, item.telegram_id] : editor.audience.ids.filter(id => id !== item.telegram_id) } })}/>
                <span>{item.name}<small>{item.telegram_id}{!item.eligible && ` · ${excludedNames[item.excluded_reason]}`}</small></span></label>)}</div>{visibleRecipients.length>100 && <small>Показаны первые 100 пользователей. Уточните поиск, чтобы найти остальных.</small>}</div>}
            {editor.kind !== "message" && <><div className="campaignSectionHeading"><h4>Вопросы</h4><span>Ответы видны владельцу панели</span></div>
              {editor.questions.map((question, index) => <details className="campaignQuestion" key={index} open={index === editor.questions.length - 1}>
                <summary>Вопрос {index + 1}<span>{question.text || (question.kind === "text" ? "Свободный текст" : "Выбор вариантов")}</span></summary><div className="campaignQuestionFields">
                <div className="campaignSectionHeading"><strong>Вопрос {index + 1}</strong>{editor.kind === "survey" && editor.questions.length > 1 && <button type="button" className="ghostButton" onClick={() => setEditor({ ...editor, questions: editor.questions.filter((_, i) => i !== index) })}>Удалить вопрос</button>}</div>
                {editor.kind === "survey" && <label>Тип ответа<select value={question.kind} onChange={event => questionPatch(index, { kind: event.target.value as Question["kind"], options: event.target.value === "text" ? [] : ["", ""], multiple: false })}><option value="choice">Выбор вариантов</option><option value="text">Свободный текст</option></select></label>}
                <label>Текст вопроса<textarea required rows={2} maxLength={300} value={question.text} onChange={event => questionPatch(index, { text: event.target.value })}/></label>
                {question.kind === "choice" && <><label>Варианты ответа, каждый с новой строки<textarea required rows={4} value={question.options.join("\n")} placeholder={"Да\nНет"} onChange={event => questionPatch(index, { options: event.target.value.split("\n") })}/><small>От 2 до 10 вариантов, до 100 символов каждый.</small></label>
                  <label className="campaignCheck"><input type="checkbox" checked={question.multiple} onChange={event => questionPatch(index, { multiple: event.target.checked })}/>Можно выбрать несколько вариантов</label></>}
              </div></details>)}
              {editor.kind === "survey" && <button type="button" className="ghostButton" disabled={editor.questions.length >= 10} onClick={() => setEditor({ ...editor, questions: [...editor.questions, newQuestion()] })}><ChatIcon name="plus"/>Добавить вопрос</button>}
              <p>Вопросы анкеты приходят по одному. Ответы сохраняются отдельно от переписки с AI.</p></>}
          </fieldset>
          <footer className="campaignActions"><button type="submit" className="ghostButton" disabled={locked}>Сохранить черновик</button>
            <button type="button" className="primaryButton" disabled={locked || !overview?.ready || !chosenCount} onClick={() => void perform(review)}>Предварительный просмотр</button>
            <button type="button" className="ghostButton" disabled={busy} onClick={() => { setEditor(undefined); setBaseline(""); }}>{dirty ? "Отменить изменения" : "Закрыть редактор"}</button></footer>
        </form> : detail ? <article className="campaignDetail">
          <div className="campaignSectionHeading"><div><span className="campaignKind">{kindNames[detail.kind]}</span><h3>{detail.title}</h3><p>{stateNames[detail.state]}</p></div>
            <div className="campaignActions">{detail.state === "draft" && <button type="button" className="primaryButton" disabled={locked} onClick={() => begin(detail)}>Редактировать</button>}
              {detail.state === "active" && <button type="button" className="ghostButton" disabled={locked} onClick={() => setConfirm("close")}>Завершить</button>}
              {detail.state !== "active" && <button type="button" className="ghostButton" disabled={locked} onClick={() => setConfirm("delete")}>Удалить</button>}</div></div>
          <div className="campaignMetrics"><div><strong>{detail.recipient_count}</strong><span>Получателей</span></div><div><strong>{detail.delivery_counts.sent ?? 0}</strong><span>Отправлено</span></div>
            <div><strong>{detail.pending_steps}</strong><span>Сообщений в очереди</span></div><div><strong>{detail.respondents}</strong><span>Ответили</span></div><div><strong>{detail.completed_responses}</strong><span>Завершили опрос</span></div></div>
          <p className="campaignMessage">{detail.text}</p>
          {((detail.delivery_counts.failed ?? 0) + (detail.step_counts.failed ?? 0) > 0 || (detail.delivery_counts.unknown ?? 0) + (detail.step_counts.unknown ?? 0) > 0) && <div className="campaignDeliveryNotice"><strong>Есть сообщения без подтверждённой отправки</strong>
            <p>Ошибок: {(detail.delivery_counts.failed ?? 0) + (detail.step_counts.failed ?? 0)}. Результат неизвестен: {(detail.delivery_counts.unknown ?? 0) + (detail.step_counts.unknown ?? 0)}. Неопределённые отправки не повторяются автоматически, чтобы не создавать дубликаты.</p></div>}
          {detail.unconfirmed_closes > 0 && <p role="status">Закрытие {detail.unconfirmed_closes} голосований ожидается или не подтверждено Telegram.</p>}
          {detail.questions.map((question, index) => <section className="campaignResult" key={index}><h4>{index + 1}. {question.text}</h4><p>Ответов: {detail.results[index]?.answered ?? 0}{question.multiple ? " · Можно выбрать несколько вариантов" : ""}</p>
            {question.options.map((option, optionIndex) => <div className="campaignVote" key={optionIndex}><span>{option}</span><strong>{detail.results[index]?.counts[optionIndex] ?? 0}</strong><progress max={Math.max(1, detail.results[index]?.answered ?? 0)} value={detail.results[index]?.counts[optionIndex] ?? 0} aria-label={option}/></div>)}</section>)}
          {Boolean(detail.answer_total) && <details className="campaignAnswerDetails"><summary>Ответы пользователей · {detail.answer_total}</summary><div className="campaignAnswers"><p>Ответы видны только владельцу панели.</p>
            {detail.answers?.map(answer => <div className="campaignAnswer" key={`${answer.telegram_id}:${answer.step}`}><strong>{names.get(answer.telegram_id) || answer.telegram_id}</strong><small>{date(answer.at)} · Вопрос {answer.step + 1}</small>
              <p>{Array.isArray(answer.answer) ? answer.answer.map(option => detail.questions[answer.step]?.options[option]).join(", ") : answer.answer}</p></div>)}
            <div className="campaignActions"><button type="button" className="ghostButton" disabled={locked || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Назад</button>
              <span>{offset + 1}–{Math.min(offset + 50, detail.answer_total)} из {detail.answer_total}</span><button type="button" className="ghostButton" disabled={locked || offset + 50 >= detail.answer_total} onClick={() => setOffset(offset + 50)}>Далее</button></div></div></details>}
          {detail.deliveries && detail.deliveries.length > 0 && <details><summary>Неотправленные сообщения и исключённые получатели</summary><div className="campaignFailures">{detail.deliveries.map(item => <p key={`${item.recipient}:${item.step}`}>{names.get(item.recipient) || item.recipient} · {item.step < 0 ? "Вступление" : `Вопрос ${item.step + 1}`} · {deliveryNames[item.state] || item.state}</p>)}</div></details>}
        </article> : <div className="campaignEmpty"><ChatIcon name="telegram"/><h3>Расскажите о важном или спросите мнение</h3><p>Выберите рассылку слева или создайте новую. Перед отправкой вы увидите текст и точное число получателей.</p></div>}
      </div>
    </div>
    {error && <NotificationNotice id="nodera:campaign-error" title="Рассылки Telegram" message={error}/>}
    {preview && <SettingsDialog title="Проверить перед отправкой" wide blocked={locked} dismissible={!busy} close={() => setPreview(undefined)} submit={() => void perform(send)} submitLabel={`Отправить ${preview.recipients.length} пользователям`}>
      <div className="campaignPreview"><span className="campaignKind">{kindNames[preview.campaign.kind]}</span><h3>{preview.campaign.title}</h3><p className="campaignMessage">{preview.campaign.text}</p>
        {preview.campaign.questions.map((question, index) => <div className="campaignQuestion" key={index}><strong>{index + 1}. {question.text}</strong>{question.kind === "text" ? <p>Пользователь ответит текстом.</p> : <ul>{question.options.map((option, i) => <li key={i}>{option}</li>)}</ul>}{question.multiple && <small>Можно выбрать несколько вариантов.</small>}</div>)}
        {preview.campaign.kind !== "message" && <p>Ответы видит владелец панели. Вопросы анкеты придут по одному.</p>}
        <strong>Получателей: {preview.recipients.length}</strong><p>{preview.recipients.slice(0, 20).map(item => item.name).join(", ")}{preview.recipients.length > 20 ? ` и ещё ${preview.recipients.length - 20}` : ""}</p>
        {preview.excluded.length > 0 && <p>Исключено недоступных получателей: {preview.excluded.length}.</p>}<p>Нажатие кнопки запустит отправку реальным пользователям Telegram.</p></div>
    </SettingsDialog>}
    {confirm && detail && <SettingsDialog title={confirm === "close" ? "Завершить рассылку?" : "Удалить рассылку и ответы?"} blocked={locked} dismissible={!busy} close={() => setConfirm(undefined)} submitLabel={confirm === "close" ? "Завершить" : "Удалить"}
      submit={() => void perform(async () => { const action = confirm; await request(`/assistant/campaigns/${detail.id}${action === "close" ? "/close" : ""}`, { method: action === "close" ? "POST" : "DELETE" }); setConfirm(undefined); if (action === "delete") { setDetail(undefined); setSelection(undefined); } else setDetail(await request<Campaign>(`/assistant/campaigns/${detail.id}`)); await load(); })}>
      <p>{confirm === "close" ? "Новые сообщения больше не будут отправляться. Отправленные голосования будут закрыты, сбор ответов прекратится. Сообщение, уже находящееся в отправке, может прийти." : "Черновик, история отправки и собранные ответы будут удалены из панели. Сообщения в Telegram останутся."}</p>
    </SettingsDialog>}
  </section>;
}
