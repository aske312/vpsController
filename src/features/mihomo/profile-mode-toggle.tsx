export function ProfileModeToggle({ label, value, active, onToggle }: { label: string; value: string; active: boolean; onToggle: () => void }) {
  return <button type="button" className="mihomoModeToggle" aria-label={`${label}: ${value}`} aria-pressed={active} onClick={onToggle}>
    <span>{label}</span><b>{value}</b>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4" /></svg>
  </button>;
}
