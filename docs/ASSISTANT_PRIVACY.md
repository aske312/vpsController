# Assistant privacy and behavior

Owner-authored Telegram announcements, polls and multi-question surveys are
managed in the panel's **Рассылки** section. Authoring, audience preview, launch,
results and deletion require panel authentication; bot transport credentials
and AI tools cannot launch campaigns. Only users of this panel's bot who have
previously contacted it are eligible. Blocked users and users who disabled
campaigns through `/notifications off` are excluded. `/notifications on` enables
future campaigns again.

Survey introductions disclose that the panel owner can read responses. Answers
are stored separately from AI conversations and are never added to model context
or chat history. Campaign definitions and individual answers are encrypted with
the API's storage key in `assistant/campaigns/campaigns.sqlite3`; recipient IDs,
delivery states and aggregate vote counts remain operational metadata. Keep this
database and `.privacy-key` together in protected backups. The owner can close
campaigns and delete their stored results; Telegram copies remain with Telegram.

Launching freezes the previewed audience. Delivery survives API restarts, obeys
Telegram rate-limit replies and checks blocking/opt-out again before each send.
An interrupted or ambiguous send is shown as unknown and is not automatically
repeated. This avoids duplicate announcements but can leave an individual survey
unfinished. Native votes allow changing or retracting a choice. Free-text answers
must reply to the corresponding question; editing that answer updates it.
NoderaBot 1.25.9 or newer is required; update the Nodera runtime bundle separately
from the application.

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
