const schedule = { enabled: false, cadence: "weekly", weekday: "Sun", hour: 4, minute: 0 };
const timer = { installed: true, active: true, last_trigger: "previous run", next_run: "next run" };
export const services = {
  items: [{ id: "api", name: "API fixture", unit: "vps-control-api.service", installed: true, unit_present: true,
    active: true, enabled: true, state: "active", substate: "running", unit_file_state: "enabled", restarts: 3,
    active_since: "now", description: "API", controls: ["restart"], disabled_controls: ["stop"],
    runtime: { state: "running", reason: "Observed", checked_at: "now" } }],
  failed_units: 0, reboot_required: false, automation_recovery_required: false,
  automation: { reboot: { ...schedule }, cleanup: { ...schedule }, update: { ...schedule } },
  timers: { reboot: { ...timer }, cleanup: { ...timer }, update: { ...timer } },
  panel_access: { mode: "vpn", public: false, vpn_urls: ["http://panel.fixture"], can_enable: true },
  service_mode: { active: false }, logging: { persistent: true, retention_days: 30, automatic_cleanup: true, disk_usage: "1 MB" },
};
export const metrics = { enabled: true, raw_hours: 24, minute_days: 7, hour_days: 90, disk_limit_mb: 64, revision: "fixture", used_bytes: 1000, trimmed_at: null, error: "" };
