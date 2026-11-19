"use client";

import { NotificationNotice } from "../../shared/notifications/notification-center";
import { useId } from "react";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import type { RuleLeaveState } from "./rule-leave-guard";

export function RuleLeaveDialog({ state, section, canSave, pending, onChoose }: {
  state: RuleLeaveState;
  section: "rules" | "settings";
  canSave: boolean;
  pending: boolean;
  onChoose: (choice: "stay" | "discard" | "save") => void;
}) {
  const id = useId();
  const ref = useDialogFocus(true, () => onChoose("stay"));
  const settings = section === "settings";
  return <div className="confirmBackdrop" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onChoose("stay");
  }}>
    <section ref={ref} tabIndex={-1} className="confirmDialog standardConfirmDialog unsavedRulesDialog" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-message`} aria-busy={state.saving}>
      <header className="standardConfirmHead">
        <div className="confirmMark" aria-hidden="true">!</div>
        <div>
          {!settings && <p className="eyebrow">НЕСОХРАНЁННЫЕ ИЗМЕНЕНИЯ</p>}
          <h2 id={`${id}-title`}>{settings ? "Сохранить настройки Mihomo?" : "Сохранить изменения правил?"}</h2>
        </div>
      </header>
      <div className="standardConfirmBody"><p id={`${id}-message`}>{pending
        ? "Результат отправленного сохранения ещё не подтверждён. Останьтесь в разделе и проверьте результат."
        : settings ? "При выходе без сохранения правки будут потеряны." : "Перед выходом из редактора сохраните правки или отмените их. Можно остаться и продолжить редактирование."}</p>
        {state.error && <NotificationNotice id="mihomo:leave-error" title={settings ? "Сохранение настроек Mihomo" : "Сохранение правил"} message={state.error} />}
      </div>
      <div className="confirmActions">
        <button data-dialog-initial type="button" disabled={state.saving} onClick={() => onChoose("stay")}>Остаться</button>
        <button type="button" disabled={state.saving || pending} onClick={() => onChoose("discard")}>{settings ? "Выйти без сохранения" : "Отменить изменения"}</button>
        <button className="confirmPrimary" type="button" disabled={state.saving || pending || !canSave} onClick={() => onChoose("save")}>{state.saving ? "Сохраняем…" : "Сохранить и перейти"}</button>
      </div>
    </section>
  </div>;
}
