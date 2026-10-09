export function ProtocolIcon({ protocol, className = "" }: { protocol: string; className?: string }) {
  const glyphs: Record<string, React.ReactNode> = {
    awg: <><path d="m5 25 11-19 11 19h-7l-4-7-4 7H5Z" /><path d="m12 25 4-7 4 7" /></>,
    hysteria2: <><path d="m18 3-12 16h9l-1 10 12-16h-9l1-10Z" /><path d="M4 8h5M23 25h5" /></>,
    tuic: <><path d="M13 6h7a10 10 0 0 1 0 20h-7" /><path d="M4 11h13M3 16h11M5 21h11" /></>,
    xray: <><path d="m16 3 12 13-12 13L4 16 16 3Z" /><path d="m10 10 12 12m0-12L10 22" /><circle cx="16" cy="16" r="3" /></>,
    "relay-agent": <><circle cx="8" cy="16" r="4" /><circle cx="24" cy="8" r="4" /><circle cx="24" cy="24" r="4" /><path d="m12 14 8-4m-8 8 8 4" /></>,
  };
  if (!glyphs[protocol]) return null;
  return <span className={`protocolIcon protocolIconVector ${protocol}${className ? ` ${className}` : ""}`} aria-hidden="true"><svg viewBox="0 0 32 32">{glyphs[protocol]}</svg></span>;
}
