# Private Relay diagnostic correlation

Relay Agent 1.6.5 adds `route_ref` and `session_ref` to bounded resource
diagnostics. The references identify forwarding activity, not a person.

- `session_ref` is a random 128-bit value created for each admitted session.
  It remains the same in active samples and the bounded recent-close sample.
- `route_ref` is the first 128 bits of HMAC-SHA256 over a versioned, canonical
  route generation: route ID, transport, listener, upstream, and credential ID.
  The HMAC key is the SHA256 digest of the existing high-entropy Relay API
  token, with the domain separator `relay-diagnostic-route-v1`.
  No new credential is sent to clients or stored in diagnostic logs.
- Controller and Agent compute the same reference. A route or credential
  generation change produces a different reference. Token rotation rekeys it.
  Bare hashes of addresses, account names, or device IDs are not used.

Relay log records contain only opaque references, numeric resource/traffic
counters, and allowlisted transport/phase values. They do not contain peer
addresses, destination addresses or ports, raw route IDs, profile/device names,
credentials, domains, request headers, or traffic contents. Existing live
forwarding configuration still necessarily contains its network endpoints;
these logs do not conceal that configuration from a host administrator.

Snapshots and memory events sample up to sixteen active/recently closed
sessions, prioritizing the largest exposed active buffers. This is a sample,
not a complete connection audit. TCP reader and writer buffer sizes are
observations of buffers exposed by asyncio, not total SSL/native allocation.
`reserved_bytes` is admission accounting, not measured per-session RSS.
UDP samples include per-session byte counters; internal QUIC streams in the
separate encrypted backhaul runtime are outside this forwarding-session scope.

Logs retain two 4 MiB segments per UTC date, for at most seven dates (56 MiB).
Heavy event volume can overwrite earlier records within a date. File mode is
0600 and the diagnostic directory is 0700. Logging failures must not interrupt
forwarding or session release.

The authenticated controller API resolves a reference using:

```text
GET /api/network/relays/{host_id}/diagnostics/{route_ref}
```

Responses use `Cache-Control: no-store`. The controller's normal live Relay
read also records observed bindings in its private
`relay/diagnostic-bindings.json`, bounded to seven days, 4096 observations, and
4 MiB. This local ledger preserves observed mappings across route deletion or
token rotation; it is never sent to a Relay. It starts recording after this
controller change is installed and a live Relay read occurs, so historical
coverage must not be assumed before the first observation.

The resolver returns configured profile/connection/device candidates. A shared
route, NAT address, or copied credential does not establish which physical
client created a session. The API always reports `client_attribution:
not_proven`, labels current versus archived configuration, and includes bounded
observed candidate history when available. To prove a specific client requires
individual authenticated ingress credentials; opaque IDs alone cannot establish
that attribution. The old incident cannot be retroactively assigned a new ID.
