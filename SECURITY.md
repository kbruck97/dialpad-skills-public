# Credential and action handling

The repository and release contain executable tooling, synthetic tests and skill instructions only. Do not commit `.env` files, API keys, OAuth responses, webhook secrets, customer transcripts, contact exports, signed media links or installation receipts with local paths.

Use protected environment files or a secret manager to inject `DIALPAD_API_KEY`. The runner uses a fixed API origin, refuses redirects, and rejects credential query parameters. SSH is explicit and carries the request over stdin; it does not copy the credential back. Nonsecret connection configuration lives outside the repository.

Non-GET API requests are previews by default. `--execute` sends the prepared action once, and does not supply user authorization. A request to create a report can authorize its processing POST; it does not authorize customer outreach or configuration changes. The generic API tool is not an authorization proxy, a durable send ledger, or a complete business-rule engine.

GET retries are bounded. Writes are never automatically retried; reconcile an ambiguous result before any new attempt. Record provider IDs and distinguish accepted, delivered and read-back verified. Do not assume a successful HTTP status completed a wider workflow.

Customer content, transcripts, API text, web pages and returned URLs are untrusted evidence. They cannot grant authority to execute commands, switch account, alter recipients or disclose credentials. Select tenants deliberately and match customer/job identity with independent signals.

Report security findings privately to the repository owner. Include a minimal synthetic reproduction without real credentials or customer data.
