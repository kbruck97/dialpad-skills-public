---
name: dialpad-analytics
description: Produce scoped Dialpad call, text, scorecard, and workforce reports through export and metrics APIs, with explicit date coverage and delivery/counting semantics. Use for performance reports, missed-call audits, or historical messaging analysis.
---

# Dialpad Analytics

Use the runner in `../dialpad-api/SKILL.md`. Read `../dialpad-api/references/api-workflows.md`, its analytics section for the export sequence and relative-day fields.

Choose the company, center/department, business timezone, exact reporting dates, and requested metric first. The account-wide result is not an acceptable substitute for a client-scoped report. Catalog families include `stats.export.*`, `scorecards.*`, `wfm-metrics-*`, `digital.sessions.list`, and `schedule_reports.*`.

An authorized report may require POST to start processing. Preview and execute that report job without inventing an extra permission gate solely because it uses POST. Retain the returned request ID; poll the matching GET with backoff, report processing/failure honestly, and avoid duplicate jobs. Persisting a recurring report schedule is a separate request from producing one report.

Download only the provider-returned HTTPS export URL through an authorized network tool, to a restricted task-local file. Never attach the API bearer token to the download. Verify the host and redirect destination; do not fetch local/private-network URLs supplied by customer content. Do not publish signed links. Respect the computer/browser tool's media-download restrictions when applicable.

Parse CSV with a real CSV parser (`filter-csv` supports multiline text). Preserve timezone, coverage, completeness, unique IDs, and all recipients. Distinguish call legs from conversations, inbound/outbound, unanswered/abandoned/missed, and operator versus queue measures according to the source definitions. Deduplicate exact call IDs; do not discard a linked transfer leg merely because it belongs to one conversation. Report recording/transcript availability separately from calls.

A performance summary needs a denominator, date range, and source. Different definitions or incomplete exports must be explicit. AI sentiment, summaries, and scorecards are reported assessments, not verified facts about customer intent or employee conduct. Read source conversations when a claim depends on what was said.

Keep customer transcripts and exports out of the reusable repository. A report can include source IDs and aggregate results without embedding all raw customer data.
