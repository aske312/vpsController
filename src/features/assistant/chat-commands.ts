export type ChatCommand = { command: string; label: string; syntax: string; insert: string; needs_args: boolean; kind: "direct" | "ai" };
export function directCommand(text: string) {
  return text.trim().startsWith("/") && !/^\/ai(?:\s|$)/i.test(text.trim());
}
export function commandMatches(items: ChatCommand[], prompt: string) {
  const query = prompt.trim().match(/^\/([^\s]*)$/)?.[1].toLowerCase() ?? "";
  return items.filter(item => item.command.slice(1).startsWith(query));
}
