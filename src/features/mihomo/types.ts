export type View = "overview" | "profiles" | "channels" | "dns" | "rules" | "routing";

export type ReadyDevice = ProfileDevice & { subscription: string; qr: string };

export type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel: string;
  phrase?: string;
  danger?: boolean;
};

export type Status = {
  active: boolean;
  core_version: string;
  profiles: number;
  profiles_in_use: number;
  credentials: number;
  channels_in_use: string[];
  channels_installed: number;
  modules_installed: number;
  modules_total: number;
  endpoint: string;
};

export type SettingField = {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "boolean";
  default: string | number | boolean;
  min?: number;
  max?: number;
  options?: Array<string | { value: string; label: string }>;
  help?: string;
};

export type Module = {
  id: string;
  name: string;
  description: string;
  category: "transport" | "dns" | "routing";
  category_name: string;
  installed: boolean;
  installable?: boolean;
  active: boolean;
  service?: string;
  settings?: SettingField[];
  connection_settings?: SettingField[];
  settings_values: Record<string, string | number | boolean>;
  installed_version?: string;
  available_version?: string;
  update_available?: boolean;
  update_breaking?: boolean;
};

export type Profile = {
  id: string;
  create_operation_id?: string;
  last_operation_id?: string;
  export_filename: string;
  name: string;
  channels: string[];
  connections: ProfileConnection[];
  routing?: Record<string, string | number | boolean>;
  devices?: ProfileDevice[];
  common_device_id?: string;
  protection_status?: Record<string, { vless_connections: number; encryption_pending: boolean; previous_connections?: number; previous_valid_until?: number; yaml_served_at?: number }>;
  subscription_status?: "active" | "obsolete" | "missing";
  created_at: string;
  updated_at: string;
};

export type ProfileConnection = {
  id: string;
  component: string;
  name: string;
  device_id: string;
  settings: Record<string, string | number | boolean>;
};

export type ProfileDevice = { id: string; name: string; scope?: "common" | "hwid" | "legacy"; hwid_hash?: string; created_at?: string; last_seen_at?: string; os?: string; os_version?: string; client_name?: string; client_version?: string; user_agent?: string; routing?: Record<string, string | number | boolean> };

export type PolicySettings = {
  schema: SettingField[];
  values: Record<string, string | number | boolean>;
  presets?: ProfilePreset[];
  rule_lists?: Array<{ id: string; key: string; title: string; description: string; default_rules: string; available_rules?: string; using_default: boolean }>;
};

export type ProfilePreset = { id: string; name: string; description: string; strategy: "fallback" | "url-test" | "select"; components: Array<{ id: string; cdn?: boolean; tls?: boolean; transport?: string; label?: string }> };

export type ConnectionStats = { active?: boolean; endpoint?: string | null; active_connections?: number; rx_bytes?: number; tx_bytes?: number; handshake_age_s?: number | null; latency_ms?: number | null };

export type ProfileStats = { summary: { configured: number; active: number; rx_bytes: number; tx_bytes: number; last_handshake_age_s: number | null; latency_ms?: number | null }; connections: Record<string, ConnectionStats>; devices?: Record<string, { configured: number; active: number; rx_bytes: number; tx_bytes: number; last_handshake_age_s: number | null; latency_ms?: number | null }> };

export type RuleIconGroup = { id: string; code: string; name: string; tokens: string[] };
