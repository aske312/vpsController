# Opaque resource diagnostic references

Agent 1.6.5 emits bounded session samples with random 128-bit `session_ref`
values and keyed 128-bit `route_ref` values. Route references use HMAC-SHA256
with the domain separator `relay-diagnostic-route-v1\0`, keyed by the SHA256
digest of the existing high-entropy controller API token. The canonical JSON
input is `[id, transport, listen_port, target_ip, target_port, credential_id]`,
using null for a missing credential ID and separators `(',', ':')`.

The controller computes the same references and retains the association with
its own configured consumers. Customer/profile/device identity is never sent
to this diagnostic recorder. The recorder strictly allows only the references,
transport/phase enums, byte counts, age, and exposed buffer sizes. Source and
destination addresses, names, domains, credentials, payloads, and raw route IDs
are excluded. Normal runtime forwarding configuration still contains required
endpoints; this protects diagnostic records, not a compromised administrator's
access to the live network configuration.

Memory events and minute snapshots include at most sixteen sessions, selecting
the largest exposed active buffers before bounded recent closes. The same
session ID also appears in the authenticated route API while that session is
among the sixteen reported references. Not all sessions are sampled. Neither
buffer observations nor admission reservations are total per-session RSS.
Closing a session retains only a small numeric record, never its streams.

The recorder uses two 4 MiB segments per UTC date and retains at most seven
dates (56 MiB). Earlier records can be overwritten under high event volume.
Diagnostics cannot prove an end-user identity on a shared route. Internal
streams in a separate QUIC backhaul process are not represented as Python
forwarding sessions. These additions preserve the existing route wire schema.
