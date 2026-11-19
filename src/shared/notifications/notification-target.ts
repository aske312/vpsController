/** Native modal dialogs make document.body inert and cover its portals. */
export function notificationTarget(): HTMLElement {
  const dialogs = document.querySelectorAll<HTMLDialogElement>("dialog:modal");
  return dialogs.item(dialogs.length - 1) ?? document.body;
}

export function subscribeNotificationTarget(changed: () => void) {
  const observer = new MutationObserver(changed);
  observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["open"] });
  return () => observer.disconnect();
}
