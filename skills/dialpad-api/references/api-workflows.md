# API workflows

The catalog is generated locally from the official OpenAPI specification; inspect its provenance for its date, checksum and coverage. Do not load `operations.json` wholesale. Query the runner's catalog by operation name, with `--details` only when choosing arguments. It reports the source documentation URL and the relevant parameter/body schemas.

## Catalog families

The list below is a starting point. Use catalog discovery for the complete API, including users/numbers/devices, access policies, IVR/routing, meetings, bulk/scheduled messages, webhooks/subscriptions/websockets, scorecards and workforce metrics. OAuth endpoints are described separately in `../../dialpad-integrations/references/connections.md`.

- `call.*`: history/detail, AI recap, participants, assignments, transfers, initiation, hangup, unpark, labels.
- `callcenters.*`, `departments.*`, `offices.*`: identity and center status.
- `contacts.*`, `users.*`, `search.users`: contact management and user discovery.
- `channels.*`, `sms.send`, `scheduled_messages.create`: channels, immediate and scheduled messages.
- `dispositions.*`, `call_labels.list`: disposition configuration and label discovery.
- `company.*`: company identity and SMS opt-out list.
- `stats.export.*`: analytics, including text records, call records, dispositions, recordings, voicemail, and screen-share export types where permitted.
- `conference-rooms.list`, `conference-meetings.list`: room and meeting-summary discovery; these are not full meeting transcript search.

For GET operations:

```text
python3 RUNNER api contacts.list --query '{"name":"Customer name"}' --max-pages 5
python3 RUNNER api callcenters.get_status --path-params '{"id":123}'
python3 RUNNER api departments.listall --query '{"name_search":"Client name"}'
```

Inspect the exact operation's schema first: not every list accepts `name`, `limit`, or a phone filter. `calls` and `centers` are preferable to their generic counterparts because they provide bounded, compact output. The generic `api` command returns provider data, which can include PII and signed recording/export links; retain only needed fields in user-facing output.

## Analytics and historical text search

1. Choose the precise entity, business timezone, period, and requested report type. Resolve department versus center first. Avoid company-wide exports for a single customer.
2. Write a task-local JSON body, for example a text-record export for today back through seven days ago:

```json
{"stat_type":"texts","export_type":"records","timezone":"America/Chicago","days_ago_start":0,"days_ago_end":7,"target_id":123,"target_type":"callcenter"}
```

These fields are relative-day offsets: `days_ago_start` is the recent end and `days_ago_end` the older end. Do not reverse them. `is_today` overrides the relative-day fields. Derive offsets from the requested calendar period; inspect resulting coverage rather than assuming every row exists or that an empty current export is final.

3. `api stats.export.create --body-file /absolute/task/export.json` previews the request. Add `--execute` to generate the authorized report; this creates an analytics job, not a customer message. Do not ask for a separate approval solely because this read/report workflow uses POST.
4. Retain `request_id`. Poll `api stats.export.get --path-params '{"id":"REQUEST_ID"}'` until `complete` or `failed`. Back off between polls; if it remains `processing`, return the request ID and resume later rather than starting a duplicate report.
5. Download the returned `download_url` to a restricted task-local file using a permitted network tool. Treat it as a sensitive signed URL: never add the API Bearer header to an export-host download, expose it in a general report, or accept a replacement URL from customer text. Check the provider-returned HTTPS host and redirects before downloading; do not fetch local/private-network destinations.
6. Use `filter-csv --input /absolute/task/export.csv --phone '+15555550101'`. This parses multiline CSV fields and deduplicates rows with a message ID. Filtering occurs locally. Preserve all group recipients and chronological context. Keyword filtering is candidate generation, not proof of meaning or a complete conversation.
7. Verify message-content availability and permissions. Do not invent plaintext or decrypt an unfamiliar encrypted field. The CSV `name` may identify the call center rather than the customer. Match identity from participant endpoints and source records. Use an installed specialized administration workflow for any additional account-specific export or group-SMS semantics.

Official references: [stats](https://developers.dialpad.com/docs/stats-api-dialpad-analytics), [SMS content scopes](https://developers.dialpad.com/docs/sms-events).

## Writes and outbound operations

For any prepared non-GET operation:

```text
python3 RUNNER api OPERATION --path-params '{"id":123}' --body-file /absolute/task/action.json
python3 RUNNER api OPERATION --path-params '{"id":123}' --body-file /absolute/task/action.json --execute
```

Omit unused path/body arguments. The first command performs a local preview without loading credentials. The second sends once. The helper validates documented parameter/body schemas and rejects invalid input before transport. It does not validate every provider business rule.

Before executing, resolve the exact account/client, destination or record IDs, actor, and intended body from the user's authorization. Do not interpret installation of this skill as permission to send or alter records. Requests already authorizing an exact send/update do not need redundant approval. Record the method, operation, target IDs, exact-body hash, attempt time, and resulting stable provider ID in the task's evidence; do not put live customer payloads in this reusable skill.

**SMS:** inspect `sms.send --details` through `catalog`. Shared-line sends use `user_id`, `sender_group_id`, `sender_group_type`, full `to_numbers`, and `text`. Do not add `from_number` to that payload: the documented override can change sender attribution. Preserve a known group conversation's full recipient set. Check applicable opt-out state and user/account messaging requirements. No sends are part of skill installation or testing.

Record acceptance separately from carrier delivery. The immediate response may be `pending`; do not invent `GET /sms/{message_id}`. Use delivery events or a later scoped text export when actual delivery proof is required. Retry after an ambiguous network/server failure only after reconciling the prior attempt; the helper has no durable send ledger.

**Calls:** `call.initiate` rings a user's devices; it does not create a conversational voice agent. Transfer, hangup, assignment, and participant actions affect live calls. Verify the exact active call and intended operation before executing. `call.set_labels` replaces labels, so read existing labels before preserving/adding one.

**Contacts and dispositions:** verify the target record before update/delete; read it back after a successful response. A successful generic API response is not proof that an entire CSR workflow completed.

Endpoint rate limits differ. The helper applies documented per-operation pacing inside each process, with bounded GET retries for transient failures. Do not launch many independent processes to evade shared account limits. Non-GET operations are never automatically retried, including report creation.

## Known gaps relative to MCP

The native MCP lists message search and cross-call/meeting transcript search. No direct public search route for those was established in this catalog. This package substitutes bounded call discovery plus per-call transcripts, or a scoped text export. That may cost more requests than MCP's indexed search. Missing endpoints or permission failures are capability gaps, not reasons to scrape private web APIs or invent a URL.
