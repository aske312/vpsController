import type { ChatCommand } from "./chat-commands";

export function ChatCommandMenu({ items, loading, error, activeIndex, onSelect, onRetry }: {
  items: ChatCommand[]; loading: boolean; error: string; activeIndex: number;
  onSelect: (item: ChatCommand) => void; onRetry: () => void;
}) {
  return <div className="mcpCommandMenu" id="mcp-command-menu" role="listbox" aria-label="Быстрые команды">
    <header><strong>Команды</strong><small>Сервер и подписки · без модели</small></header>
    {loading && !items.length ? <p role="status">Загрузка команд…</p> : error ? <p role="alert">{error} <button type="button" className="ghostButton" onClick={onRetry}>Повторить</button></p>
      : items.length ? items.map((item, index) => <button type="button" role="option" id={`mcp-command-${index}`} key={item.command}
        aria-selected={index === activeIndex} tabIndex={index === activeIndex ? 0 : -1} onMouseDown={event => event.preventDefault()} onClick={() => onSelect(item)}>
        <code>{item.syntax}</code><span>{item.label}{item.kind === "ai" ? " · использует модель" : ""}</span>
      </button>) : <p>Команда не найдена. /help — справка.</p>}
  </div>;
}
