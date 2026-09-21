---
name: dialpad-messaging
description: Find Dialpad message history and prepare or execute authorized SMS, scheduled messages, bulk messages, and contact updates using the packaged API runner. Use for texting and message/contact workflows.
---

# Dialpad Messaging

Read `../dialpad-api/SKILL.md` for the runner and connection. Discover the exact operation with `catalog --search ... --details`; the catalog defines current fields, not a remembered example.

## Message history

The public REST specification has SMS send but no general inbox or indexed SMS search endpoint. Prefer an already connected native MCP message-search tool when available. Otherwise use a scoped text export through `../dialpad-analytics/SKILL.md`, then `filter-csv` by complete phone number and relevant date/context. No search hit means no match within the inspected coverage, not proof that no conversation exists. Preserve all group recipients and related messages.

## Send or schedule

1. Resolve the intended company, user/shared line, complete recipient list, message content, and timing from the request and current records. Read contact details with `contacts.*` where needed; a name alone is insufficient. Check applicable opt-out state, consent, and account messaging requirements. Read `company.sms_opt_out` when permitted; do not change subscriptions to make a send succeed.
2. Inspect `sms.send`, `scheduled_messages.*`, or `bulk_messages.*`. For immediate `sms.send`, shared-line sends use the documented sender-group fields and acting user; do not add a from-number override casually. Scheduled and bulk operations have different sender schemas: inspect their documented `from_number`/`user_id` requirements, ownership and messaging eligibility. Do not assume shared-line sender-group fields work there. Preserve an existing group conversation's recipients. A request to contact one person does not authorize bulk outreach.
3. Write the exact authorized body to a restricted task-local JSON file. Preview `api OPERATION --body-file ...` and check sender, recipients, content, and timezone. Execute once when the user has authorized the action. For schedules, distinguish local time, UTC, and account time; inspect existing schedules before creating duplicates.
4. Keep the stable returned message/schedule ID, attempt time, and payload hash. Read back scheduled/bulk records where supported. Immediate API acceptance is not delivery: use provider delivery events or later scoped exports for delivery evidence. There is no invented `GET /sms/{id}`.
5. After an unknown transport/server outcome, reconcile the existing attempt before retrying. This package has no durable send ledger or provider-independent idempotency guarantee. Cancel or update only the intended schedule, not all messages to a phone number.

## Contacts

Resolve contacts by account plus independent signals before create/update/delete. Prefer update when a matching record already exists. `contacts.create_with_uid` has upsert semantics; inspect the supplied external identity and effect. Read the specific record back after a write. Return only contact data needed for the task.
