# Relay memory diagnostics (1.6.6)

The serving process starts CPython `tracemalloc` with four allocation frames.
No debugger, heap dump, object representation, task name, exception text, source
line, local variable, request body, or credential is written to disk. Code
locations are restricted to Agent sources and the Python standard library.

## Files and collection

`/var/lib/vps-control-relay-agent/diagnostics/relay-YYYY-MM-DD.jsonl` keeps the
existing resource/session records. `memory-YYYY-MM-DD.jsonl` independently keeps
`memory_detail` records. Each family retains two 4 MiB segments per UTC date,
at most seven dates: 112 MiB combined maximum. Heavy volume can overwrite older
records within a date. The daily prune command/timer handles both families.
Existing private-directory and file modes, symlink protection and fail-open
logging apply to both. Copy both families and journal/kernel OOM entries before
rotation. Snapshots are sampled observations, not a full connection audit.

Resource samples every five seconds now include traced Python bytes/peak,
tracer metadata bytes, allocated Python blocks, Python/OpenSSL version numbers,
anonymous/file RSS, private dirty/anonymous/swap memory (when procfs provides
them), ready/scheduled event-loop handle counts, and observed event-loop delay.
RSS minus traced bytes is **not** a precise native-heap measurement: RSS also
includes interpreter, allocator retention, mappings and other allocations.

Detailed records are collected at startup and every five minutes normally.
Growth of at least 16 MiB RSS or 1 MiB tracer metadata since the last detail,
or host available memory below 96 MiB, permits a detail every five seconds.
Heap allocation summaries are limited to once per 15 seconds during growth;
other forensic observations can still be recorded every five seconds.

Each detail contains:

- The sixteen largest traced allocation stacks, with bytes and block counts.
  At most four code locations per stack are retained. Previous size and delta
  are present only if the stack occurred in the previous top-sixteen summary;
  null means no comparable baseline, not zero previous allocation. Native
  OpenSSL/libc allocations are outside tracemalloc coverage.
- Up to sixteen grouped task await chains (eight code locations each), counts,
  and up to sixteen opaque route/session bindings per group. Scanning is capped
  at 4096 tasks and 64 distinct groups; total/scanned counts expose truncation.
- Grouped ready/scheduled callbacks by code location, with the same scan/group
  bounds. Unknown or native callbacks are counted with empty code locations;
  callback arguments and representations are never inspected or logged.
- GC generation counts and collection totals. No full GC object census runs.
- Session samples additionally observe TLS incoming/outgoing BIO bytes, TLS
  read buffer capacity and write-backlog bytes/items, plus transport closing
  and read-paused flags. These private asyncio attributes are best-effort and
  may be absent on another Python runtime. They exclude native SSL heap memory.

## Overhead and failures

Tracing has per-allocation CPU and memory costs; these are not zero-cost or
hard real-time probes. Every five-second sample checks tracing metadata; above
16 MiB tracing stops and its history is discarded. Restart is attempted after
five minutes with adequate host headroom. The threshold is a sampled guard,
not a hard maximum between samples. Last saved summaries survive tracing stops.
An allocating heap snapshot is skipped below 96 MiB host available memory;
resource, task, callback and TLS observations continue. Snapshot duration is
recorded as `heap_duration_ms` so production overhead can be assessed. A
forensic failure increments logging failures and does not suppress the ordinary
resource record or interrupt forwarding/admission recovery.

`python_trace_state` / `detail.trace_state`: 0 unavailable, 1 tracing, 2 stopped
at the overhead cap. `heap_skip_reason`: 0 snapshot taken, 1 low host headroom,
2 tracing unavailable/stopped, 3 snapshot interval not due.

## Investigating the next incident

Match PID/version and UTC timestamps across both file families and journal.
Compare RSS growth to traced bytes, then inspect allocation code locations and
block counts. If traced bytes stay flat, examine callback/task counts and TLS
BIO/backlog samples; persistent unexplained anonymous RSS calls for isolated
native allocation profiling. Opaque route/session bindings identify observed
forwarding activity, not a proven physical client or the cause of an allocation.
Use the exact installed 1.6.6 source to resolve recorded line numbers.
