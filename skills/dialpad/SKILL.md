---
name: dialpad
description: Route Dialpad work to the packaged API, customer conversation, messaging, analytics, administration, and integration skills. Use for broad Dialpad requests or when choosing the right Dialpad workflow.
---

# Dialpad Skill Pack

Choose the smallest workflow and load only its instructions. The pack supplies executable public API operations and agent procedures; account permissions and connected MCP tools determine what is available in a particular session.

| Request | Load |
| --- | --- |
| Calls, transcripts, recaps, contacts, a specific API operation | `../dialpad-api/SKILL.md` |
| Customer callback, prior promises, handoff | `../dialpad-callback-brief/SKILL.md` |
| SMS, scheduled/bulk messages, message history, contact updates | `../dialpad-messaging/SKILL.md` |
| Call/text reporting, scorecards, agent performance | `../dialpad-analytics/SKILL.md` |
| Users, numbers, offices, routing, IVR, contact centers, live call controls | `../dialpad-administration/SKILL.md` |
| Webhooks, event subscriptions, OAuth, native MCP, Zapier | `../dialpad-integrations/SKILL.md` |

Use direct API by default when its documented operation fits. Use an already connected native MCP tool for indexed message or transcript search when available; discover its actual schema first. A Zapier connection exposes only its configured tools. Installation does not connect either MCP service.

Resolve the company and customer before retrieving content or acting. When a request needs connected CRM or operational records, use the available authorized tools and state any missing access or evidence. Do not silently substitute another account or claim Dialpad proves a job status recorded elsewhere.

The generic API runner previews mutations until `--execute`. That flag is execution mechanics, not user authorization. Preserve existing authorization and avoid redundant questions for an exact authorized action. Record acceptance, delivery, and readback as separate outcomes.
