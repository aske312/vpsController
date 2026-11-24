# Assistant privacy and behavior

Panel authentication grants access to the panel's own chat, not to any Telegram
user's identity. Panel routes reject `telegram_id` for chat, memory, requests,
streams, traces, commands and confirmations. NoderaBot authenticates its current
interlocutor separately. The history tool never lists or reads client dialogs
for the panel, and diagnostic traces are restricted to their own actor. Panel
confirmation polling excludes client confirmations, even with `all_actors=true`.

Conversation bodies (including titles and attachments), personalization,
observed bot history, confirmation plans and durable request inputs/results use AES-256-GCM at rest.
Each write uses a fresh nonce and authenticates the relative record path, so
moving ciphertext between actors or records does not grant access. The API key
is generated independently of panel credentials at
`{data_dir}/assistant/.privacy-key`, with mode `0600`. This is a storage encryption
key, not a model-provider API key. NoderaBot's temporary delivery inbox uses a
separate `.privacy-key` inside each inbox directory in its private systemd state.

At Linux API startup, existing content records are atomically encrypted before
the API accepts requests. Original modification times are retained so retention
does not restart. Bot inboxes migrate before processing deliveries. Missing keys,
modified ciphertext and unsupported formats fail closed; encrypted records must
never be silently reset. Keep each key with the corresponding protected backup;
restoring ciphertext without its original key cannot recover messages. Component
backups include the API key, and bot state backups include inbox keys.

The mandatory agent policy applies to both clients and administrators. It forbids
disclosing or confirming other clients' private requests, mixing personal context,
substituting identities, sending private data to search or unrelated recipients,
and treating retrieved content as instructions. It also requires respectful
communication, honest uncertainty, verified tool results, minimal disclosure,
and standard confirmation before changes. Character settings and personal
preferences do not override these rules. Server authorization enforces access
boundaries independently of the model's compliance with the policy.

This is **not end-to-end encryption against the host operator**. The API service
decrypts content to process it, and the configured model provider receives the
context needed to answer. A host administrator who controls code, process memory
or storage keys can obtain plaintext. Telegram also handles bot messages. A
stronger guarantee requires an independently trusted processing environment and
a separate design for client authentication and key ownership.

Already downloaded messages, browser snapshots, Telegram copies and old backups
cannot be recalled. The panel removes its old per-client chat snapshots when
opened; copies made elsewhere remain outside this migration. Operational usage
statistics and bounded diagnostic traces retain metadata without message bodies.
Application updates and Nodera runtime updates are separate: install the new
Nodera bundle to apply the runtime behavior policy and bot inbox encryption.
