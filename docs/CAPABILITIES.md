# Capability and verification matrix

The official specification checked on 2026-09-21 contains 241 standard REST operations and three OAuth endpoints. Generate the catalog locally with `scripts/update_catalog.py`; the generated metadata records its exact source checksum and counts. A catalog refresh does not prove newly introduced operations work until their inputs and behavior are verified.

| Area | Pack route | Boundary |
| --- | --- | --- |
| Concluded calls, details, transcripts, AI recaps | Scoped helpers and API | Transcript/recap availability and account scopes vary |
| Multi-call transcript lookup | Bounded call discovery and transcript reads | Explicit scan coverage; not equivalent to an indexed global search |
| Contacts, users, company, offices, departments, centers | API catalog | Correct account and role required; generic API is not a tenant authorization proxy |
| SMS, bulk/scheduled messages, channels | API + messaging skill | No generic REST inbox/search or invented message GET; delivery needs separate evidence |
| Analytics, text records, scorecards, WFM/digital metrics | API + analytics skill | Async exports, scope/license limitations, sensitive download URLs |
| User/number/device assignment, routing, IVR, access policies | API + administration skill | Authorization and precise readback required; not every UI setting has a public API |
| Live call controls and callbacks | API catalog | Real call effects; calling a device is not an autonomous voice agent |
| Meetings, conference summaries/rooms | API catalog | Catalog does not establish full meeting transcript retrieval/search |
| Webhooks, event subscriptions, websockets | API + integrations skill | Receiver deployment/signature handling is a separate integration; registration is not delivery |
| OAuth authorize/redeem/revoke | Separate documented auth flow | Three OAuth endpoints are inventoried but excluded from generic bearer execution |
| Native Dialpad MCP search | Existing host connector + workflow rules | Requires enrollment/login; discover actual session tools; not installed by this pack |
| Zapier Dialpad connected MCP | Existing configured Zapier tools | Published actions do not establish what a particular connection exposes |
| Callback briefs with operational context | Conversation workflow + optional connected records | External system credentials and clients are not supplied by this pack |

## Evidence levels

- **Cataloged:** official operation and schema are available in the locally generated catalog.
- **Implemented:** the runner can form the documented request and apply its transport rules.
- **Fixture-tested:** automated synthetic tests exercise schema, transport, pagination, failure and packaging behavior.
- **Live-read verified:** a specific permitted read was executed against a selected account. Offline tests and installation do not establish this level.
- **Live mutation verified:** an authorized mutation plus readback/delivery evidence exists. Installation and synthetic tests do not establish this level.

No general speed claim against MCP is made. A scoped helper saves model context and repeated implementation, while indexed MCP search may require fewer requests for broad historical searches. Compare the same task, scope, coverage and success criteria before declaring either faster.

## Product/source limits

Catalog generation covers the standard `/api/v2` operations in the [official OpenAPI collection](https://dialpad.com/static/openapi/platform-v1.0.json). The [native MCP guide](https://developers.dialpad.com/docs/dialpad-mcp-server) describes a separate connection; the live tool list determines its capabilities in a session. [Zapier's published actions](https://zapier.com/mcp/dialpad) do not establish that those tools are enabled in a particular user's server.

Original tooling and provider schemas have distinct licensing; see [third-party notices](../THIRD_PARTY_NOTICES.md).
