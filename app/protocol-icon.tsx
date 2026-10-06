/* eslint-disable @next/next/no-img-element */
const protocolAssets: Record<string, string> = {
  wg: "WG",
  awg: "AWG",
  hysteria2: "HY2",
  tuic: "TUIC",
  xray: "VLESS",
};

export function ProtocolIcon({ protocol, className = "" }: { protocol: string; className?: string }) {
  const asset = protocolAssets[protocol];
  if (protocol === "relay-agent") return <span className={`protocolIcon protocolIconVector relay${className ? ` ${className}` : ""}`} aria-hidden="true"><svg viewBox="0 0 32 32"><circle cx="8" cy="16" r="4" /><circle cx="24" cy="8" r="4" /><circle cx="24" cy="24" r="4" /><path d="m12 14 8-4m-8 8 8 4" /></svg></span>;
  if (!asset) return null;
  return <img className={`protocolIcon${className ? ` ${className}` : ""}`} src={`/protocol-${asset}.webp`} alt="" aria-hidden="true" />;
}
