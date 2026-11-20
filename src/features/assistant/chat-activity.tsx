"use client";

import { useEffect, useState } from "react";

export function ChatActivity({ label, startedAt }: { label: string; startedAt?: number }) {
  const [start] = useState(() => startedAt ?? Date.now());
  const [now, setNow] = useState(start);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - start) / 1000));
  const time = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  return <div className="mcpThinking" role="status">
    <span className="mcpActivitySignal" aria-hidden="true"><i/><i/><i/><i/><i/></span>
    <span className="mcpActivityLabel" key={label}>{label}</span>
    <span className="mcpActivityClock" aria-hidden="true" title="Время с отправки запроса">{time}</span>
  </div>;
}
