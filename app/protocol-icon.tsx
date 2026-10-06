/* eslint-disable @next/next/no-img-element */
const protocolAssets: Record<string, string> = {
  wg: "WG",
  awg: "AWG",
  hysteria2: "HY2",
  tuic: "TUIC",
  trojan: "TRJ",
};

export function ProtocolIcon({ protocol, className = "" }: { protocol: string; className?: string }) {
  const asset = protocolAssets[protocol];
  if (!asset) return null;
  return <img className={`protocolIcon${className ? ` ${className}` : ""}`} src={`/protocol-${asset}.webp`} alt="" aria-hidden="true" />;
}
