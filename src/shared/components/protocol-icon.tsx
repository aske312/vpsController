/* eslint-disable @next/next/no-img-element */
const protocolAssets: Record<string, string> = {
  wg: "WG", awg: "AWG", shadowsocks: "SS", hysteria2: "HY2", tuic: "TUIC",
  trojan: "TRJ", openvpn: "OVPN", ikev2: "IKE", mihomo: "Mi",
  "vless-reality-xhttp": "VLESS", "transport-wg": "WG", "transport-awg": "AWG",
  "transport-shadowsocks": "SS", "transport-hysteria2": "HY2", "transport-tuic": "TUIC",
  "transport-reality": "VLESS",
};

export function ProtocolIcon({ protocol, className = "" }: { protocol: string; className?: string }) {
  const asset = protocolAssets[protocol] || protocolAssets[protocol.replace(/^protocol-/, "")];
  if (!asset) return null;
  return <img className={`protocolIcon${className ? ` ${className}` : ""}`} src={`/icons/${asset}.webp`} alt="" aria-hidden="true" />;
}
