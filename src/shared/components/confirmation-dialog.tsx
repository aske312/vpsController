"use client";

import { useId, useState } from "react";
import type { ConfirmationRequest } from "../types/control-plane";
import { useDialogFocus } from "./use-dialog-focus";

export function ConfirmationDialog({ request, onClose }: {
  request: Omit<ConfirmationRequest, "resolve">;
  onClose: (confirmed: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const id = useId();
  const ref = useDialogFocus(true, () => onClose(false));
  return <div className="confirmBackdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(false); }}>
    <section ref={ref} tabIndex={-1} className={`confirmDialog standardConfirmDialog ${request.danger ? "danger" : ""}`} role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-message`}>
      <header className="standardConfirmHead">
        <div className="confirmMark" aria-hidden="true">{request.danger ? "!" : "✓"}</div>
        <div><p className="eyebrow">ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</p><h2 id={`${id}-title`}>{request.title}</h2></div>
      </header>
      <div className="standardConfirmBody">
        <p id={`${id}-message`}>{request.message}</p>
        {request.phrase && <label>Для подтверждения введите <strong>{request.phrase}</strong>
          <input value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" />
        </label>}
      </div>
      <div className="confirmActions">
        <button data-dialog-initial type="button" onClick={() => onClose(false)}>{request.cancelLabel || "Отмена"}</button>
        <button className="confirmPrimary" type="button" disabled={Boolean(request.phrase && value !== request.phrase)} onClick={() => onClose(true)}>{request.confirmLabel}</button>
      </div>
    </section>
  </div>;
}
