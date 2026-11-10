/* eslint-disable @next/next/no-img-element */
const protocolAssets: Record<string, string> = {
  wg: "WG", awg: "AWG", shadowsocks: "SS", hysteria2: "HY2", tuic: "TUIC",
  trojan: "TRJ", openvpn: "OVPN", ikev2: "IKE", mihomo: "Mi",
  "vless-reality-xhttp": "VLESS", "transport-wg": "WG", "transport-awg": "AWG",
  "transport-shadowsocks": "SS", "transport-hysteria2": "HY2", "transport-tuic": "TUIC",
  "transport-reality": "VLESS",
};

export function ProtocolIcon({ protocol, className = "" }: { protocol: string; className?: string }) {
  if (protocol === "ai-agent") return <svg className={`protocolIcon ${className}`} viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="16" cy="16" r="5"/><circle cx="6" cy="6" r="3"/><circle cx="26" cy="6" r="3"/><circle cx="16" cy="28" r="3"/><path d="m8 8 4 4m12-4-4 4m-4 9v4"/></svg>;
  if (protocol === "nodera-bot") return <svg className={`protocolIcon ${className}`} viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="5" y="9" width="22" height="18" rx="5"/><path d="M16 9V4m-6 13h3m6 0h3m-10 6h8"/></svg>;
  const asset = protocolAssets[protocol] || protocolAssets[protocol.replace(/^protocol-/, "")];
  if (!asset) return null;
  return <img className={`protocolIcon${className ? ` ${className}` : ""}`} src={`/icons/${asset}.webp`} alt="" aria-hidden="true" />;
}
