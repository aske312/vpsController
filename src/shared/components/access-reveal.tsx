"use client";

import { useId, useState, type ReactNode } from "react";

export function AccessReveal({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(false);
  const id = useId();
  return <div className="accessReveal">
    <p>QR-код содержит данные доступа. Показывайте его только владельцу устройства.</p>
    <button type="button" aria-expanded={visible} aria-controls={id} onClick={() => setVisible((value) => !value)}>{visible ? "Скрыть QR-код" : "Показать QR-код"}</button>
    <div id={id}>{visible && children}</div>
  </div>;
}
