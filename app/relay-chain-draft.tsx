"use client";

import { useEffect, useState } from "react";

type Draft = { name: string; source: string; transport: "tcp" | "udp"; listenPort: string; target: string; targetPort: string };
const empty: Draft = { name: "", source: "", transport: "tcp", listenPort: "20000", target: "", targetPort: "20000" };

export function RelayChainDraft({ node, disabled }: { node?: string; disabled: boolean }) {
  const [draft, setDraft] = useState<Draft>(empty);
  const [readyKey, setReadyKey] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const key = node ? `relay-chain-draft:v1:${node}` : "";
  useEffect(() => {
    const timer = setTimeout(() => {
      setReadyKey(""); setDraft(empty); setMessage(""); setError("");
      if (!key) return;
      try {
        const saved = localStorage.getItem(key);
        if (saved) {
          const value = JSON.parse(saved) as Draft;
          if (value && typeof value === "object" && Object.keys(empty).every(field => typeof value[field as keyof Draft] === "string") && ["tcp", "udp"].includes(value.transport)) {
            setDraft(value); setMessage("Заготовка загружена. Пересылка не запущена.");
          }
        }
        setReadyKey(key);
      } catch { setError("Не удалось прочитать заготовку из этого браузера."); setReadyKey(key); }
    }, 0);
    return () => clearTimeout(timer);
  }, [key]);
  function change<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft(current => ({ ...current, [field]: value })); setMessage(""); setError("");
  }
  const locked = disabled || readyKey !== key || !key;
  return <article className="panel agentChain" aria-labelledby="agent-chain-title">
    <div className="panelHead"><div><p className="eyebrow">ЦЕПОЧКА RELAY</p><h2 id="agent-chain-title">Пересылка с другого агента</h2></div><span className="agentTag">Заготовка</span></div>
    <p className="agentDescription">Другой Relay Agent → этот узел → сервер назначения. Сохранение не создаёт маршруты и не запускает пересылку. Заготовка хранится только в этом браузере для выбранного узла.</p>
    <form onSubmit={event => {
      event.preventDefault(); if (locked) return;
      try { localStorage.setItem(key, JSON.stringify(draft)); setMessage("Заготовка сохранена в этом браузере. Пересылка не запущена."); setError(""); }
      catch { setError("Не удалось сохранить заготовку. Проверьте доступность хранилища браузера."); }
    }}>
      <fieldset disabled={locked} className="agentChainFields">
        <label>Название цепочки<input required maxLength={64} value={draft.name} onChange={event => change("name", event.target.value)} placeholder="Например, вход через Amsterdam" /></label>
        <label>IP или домен другого Relay Agent<input required maxLength={253} pattern="[^\s/@?#:]+|[a-fA-F0-9:]+" spellCheck={false} value={draft.source} onChange={event => change("source", event.target.value)} placeholder="relay.example.com" /></label>
        <label>Транспорт<select value={draft.transport} onChange={event => change("transport", event.target.value as Draft["transport"])}><option value="tcp">TCP</option><option value="udp">UDP</option></select></label>
        <label>Входящий порт этого узла<input required type="number" min={20000} max={20999} value={draft.listenPort} onChange={event => change("listenPort", event.target.value)} /></label>
        <label>IP или домен назначения<input required maxLength={253} pattern="[^\s/@?#:]+|[a-fA-F0-9:]+" spellCheck={false} value={draft.target} onChange={event => change("target", event.target.value)} placeholder="origin.example.com" /></label>
        <label>Порт назначения<input required type="number" min={1} max={65535} value={draft.targetPort} onChange={event => change("targetPort", event.target.value)} /></label>
      </fieldset>
      <div className="agentServiceButtons"><button className="primaryButton" type="submit" disabled={locked}>Сохранить заготовку</button><button type="button" disabled={locked} onClick={() => {
        try { localStorage.removeItem(key); setDraft(empty); setError(""); setMessage("Заготовка удалена из этого браузера."); }
        catch { setError("Не удалось удалить заготовку из хранилища браузера."); }
      }}>Удалить заготовку</button></div>
    </form>
    {error && <p className="errorBox" role="alert">{error}</p>}
    {message && <p className="agentNotice" role="status">{message}</p>}
  </article>;
}
