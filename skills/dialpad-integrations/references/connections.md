# Connection choices

Checked 2026-09-21. Discover actual connected tools rather than relying on a product tool-count claim.

## Direct API

The packaged runner uses a selected API key or existing access token as a Bearer header, via local execution or explicitly configured SSH. See `../../dialpad-api/references/connection.md`. Keys, token responses, webhook secrets and signed URLs are runtime data; never commit them.

## OAuth

The official specification separately defines `/oauth2/authorize`, `/oauth2/token` and `/oauth2/deauthorize`. These are credential lifecycle endpoints, not ordinary `/api/v2` account actions; the generic API runner deliberately does not issue them. Configure OAuth through Dialpad's supported client/application flow, using the registered redirect URI, requested scopes, state validation and confidential-client requirements in the current official documentation. Do not invent a headless login or paste a token into chat. An already obtained token may be supplied through the selected protected credential source. Refresh, revoke and application registration remain the host's credential-management responsibility.

## Native Dialpad MCP

The [developer guide](https://developers.dialpad.com/docs/dialpad-mcp-server) describes hosted MCP with account login and early-access enrollment. Use the official regional endpoint from that guide through the agent host's supported connector configuration. Do not put a guessed endpoint, a token, or a session secret in this package. Complete user login through the host's normal flow.

Once connected, list tools and inspect their schemas. Prefer native search for indexed messages or call/meeting transcripts when it supports the requested scope. Preserve identity matching, evidence and authorization rules from the API workflows. A 403 enrollment/permission error is not an empty result. Published marketing and developer tool counts differ; actual session discovery determines availability.

## Zapier connected MCP

[Zapier's Dialpad MCP listing](https://zapier.com/mcp/dialpad) currently advertises contact creation/assignment and an authenticated API Request action. The [app integration](https://zapier.com/apps/dialpad/integrations) also lists call-state and SMS triggers; a trigger is not automatically an on-demand MCP search tool.

Use the user's existing authenticated Zapier server when it exposes the required action. Inspect its live schema and configured connection identity. For API Request, use the corresponding official method/path/schema from this pack; avoid arbitrary origins or credential forwarding. Do not invent a Zapier send-SMS or transcript-search tool absent from discovery. Creating a Zap or enabling a trigger requires its own requested workflow and delivery destination.

MCP routes are optional host connections. This repository neither runs a new MCP server nor claims those connections have been installed merely because the skills are present.
