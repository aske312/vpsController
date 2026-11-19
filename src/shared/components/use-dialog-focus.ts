"use client";

import { useEffect, useRef } from "react";

const dialogs: Array<{ element: HTMLElement; previous: HTMLElement | null }> = [];

/** Only the topmost dialog owns Escape and keyboard focus. */
export function useDialogFocus<T extends HTMLElement = HTMLElement>(open: boolean, onClose: () => void) {
  const ref = useRef<T>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const entry = { element: dialog, previous };
    dialogs.push(entry);
    const selector = 'button, input, select, textarea, a[href], [tabindex]';
    const dock = () => document.querySelector<HTMLElement>(".gateNotificationDock");
    const contains = (target: Node | null) => Boolean(target && (dialog.contains(target) || dock()?.contains(target)));
    const ownsFocus = () => {
      const native = document.querySelector("dialog:modal");
      return dialogs.at(-1) === entry && (!native || native.contains(dialog));
    };
    const elements = () => [...new Set([...dialog.querySelectorAll<HTMLElement>(selector), ...dock()?.querySelectorAll<HTMLElement>(selector) ?? []])]
      .filter((element) => element.tabIndex >= 0 && !element.matches(':disabled, [hidden]') && element.getClientRects().length > 0);
    (dialog.querySelector<HTMLElement>('[data-dialog-initial]') ?? elements()[0] ?? dialog).focus();
    const keydown = (event: KeyboardEvent) => {
      if (!ownsFocus()) return;
      if (event.key === "Escape") {
        if (dock()?.contains(event.target as Node)) return;
        event.preventDefault(); event.stopImmediatePropagation(); close.current();
      } else if (event.key === "Tab") {
        const items = elements();
        const first = items[0] ?? dialog;
        const last = items.at(-1) ?? dialog;
        if (!contains(document.activeElement) || event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last || !items.length) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      }
    };
    const focusin = (event: FocusEvent) => {
      if (ownsFocus() && !contains(event.target as Node)) (elements()[0] ?? dialog).focus();
    };
    document.addEventListener("keydown", keydown, true);
    document.addEventListener("focusin", focusin);
    return () => {
      document.removeEventListener("keydown", keydown, true);
      document.removeEventListener("focusin", focusin);
      const topmost = dialogs.at(-1) === entry;
      const index = dialogs.indexOf(entry);
      for (const nested of dialogs.slice(index + 1)) {
        if (nested.previous && dialog.contains(nested.previous)) nested.previous = entry.previous;
      }
      if (index >= 0) dialogs.splice(index, 1);
      if (topmost && entry.previous?.isConnected) entry.previous.focus();
    };
  }, [open]);
  return ref;
}
