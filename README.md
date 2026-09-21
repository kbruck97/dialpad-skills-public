# Dialpad Skill Pack

Version 1.1.0 · Python 3.10+ · Codex and Hermes · Standard-library runtime · MIT

Portable agent skills for retrieving Dialpad conversations, preparing callback briefs, and performing authorized API operations. The pack has seven focused skills and a schema-guided runner. Generate the API catalog locally from Dialpad's official specification before installation; provider schemas are not included in this repository or its release archives.

## Included skills

| Skill | Purpose |
| --- | --- |
| `dialpad` | Route a request to the right workflow |
| `dialpad-api` | Schema-guided API execution, account checks, calls, transcripts, recaps and scoped search |
| `dialpad-callback-brief` | Customer needs, prior promises, current context and handoffs |
| `dialpad-messaging` | Message history, contacts, authorized SMS and scheduled/bulk messages |
| `dialpad-analytics` | Call/text exports, scorecards, workforce metrics and reporting |
| `dialpad-administration` | Users, numbers, offices, queues, routing, IVR, meetings and live call controls |
| `dialpad-integrations` | Webhooks/subscriptions, websockets, OAuth, native MCP and Zapier guidance |

## Coverage

The specification checked on 2026-09-21 contains **244 operations: 241 standard REST operations supported by the generic runner, plus three OAuth endpoints handled by separate authentication flows**. Catalog generation records the exact source checksum and coverage. See [capabilities](docs/CAPABILITIES.md) and [verification](docs/VERIFICATION.md) for the limits of these claims.

This is catalog coverage of that specification. It does not establish that every Dialpad product feature is public API, every endpoint is permitted for every account, or every operation has been exercised live. Native MCP indexed message/transcript search requires that separate connection. The pack cannot grant missing API scopes, licenses, enrollment or access to other business systems.

## Bootstrap and install

From the repository or extracted release root, generate the provider catalog and preview the installation:

```sh
python3 scripts/update_catalog.py
python3 scripts/install.py list
python3 scripts/install.py install --runtime codex --target-root "$HOME/.codex/skills" --dry-run
python3 scripts/install.py install --runtime codex --target-root "$HOME/.codex/skills"
```

Catalog bootstrap downloads the official public specification; it does not use an API key or customer records. For an offline import, supply `--source-file /path/to/platform-v1.0.json`. Generated provider files stay local and are excluded from source control and distribution archives. See [third-party notices](THIRD_PARTY_NOTICES.md).

For Hermes, specify the skills root visible to the running agent or its corresponding host bind mount. Do not assume a host path is accessible inside a container.

```sh
python3 scripts/install.py install --runtime hermes --target-root /path/to/hermes/skills/business-operations --dry-run
python3 scripts/install.py install --runtime hermes --target-root /path/to/hermes/skills/business-operations
python3 scripts/install.py check --target-root /path/to/hermes/skills/business-operations
```

An existing installation requires `--replace` for changed skill files. The installer makes backups and a receipt, preserves unrelated skills, and supports rollback with drift checks. Its state lives under `<target-root>/.archive/dialpad-skill-pack`, which Hermes excludes from skill discovery. Individual skill replacements are atomic; an interrupted multi-skill installation may require inspecting its receipt and lock before resuming. Use the installer's subcommand help for receipt and rollback arguments. Refresh the agent's skill discovery if its host caches the list. Installation does not configure MCP, create an automation or send a message.

## Connect

Keep credentials outside this repository. The runtime accepts `DIALPAD_API_KEY`, a selected environment file, or an explicit SSH connection. A protected JSON configuration stores only paths and transport settings:

```json
{"via":"local","env_file":"/protected/dialpad.env"}
```

Save it as `~/.config/dialpad-skills/connection.json`, or select it with `--config`. Hermes uses `$HERMES_HOME/.config/dialpad-skills/connection.json` when configured; XDG configuration is also supported. For an existing SSH credential host:

```json
{"via":"ssh","ssh_host":"authorized-dialpad-host","remote_env_file":"/protected/dialpad.env"}
```

```sh
python3 skills/dialpad-api/scripts/dialpad.py doctor
python3 skills/dialpad-api/scripts/dialpad.py doctor --live
python3 skills/dialpad-api/scripts/dialpad.py catalog --search contacts
python3 skills/dialpad-api/scripts/dialpad.py catalog --search contacts.list --details
```

`doctor` without `--live` makes no Dialpad API call; SSH mode still connects to the selected credential host. API keys never belong in command arguments. See [connection settings](skills/dialpad-api/references/connection.md).

## Examples to ask the agent

- “Find this customer's last call to our support team and tell me what we promised.”
- “Prepare a callback brief and compare it with the connected customer record.”
- “Summarize missed calls for this department last week.”
- “Draft a reply to this customer.” Drafting does not send.
- “Show me the proposed routing change for this contact center.”

The pack has no required CRM dependency. If a task needs another system, the agent must use an authorized connection and disclose missing or conflicting evidence.

## Maintenance and testing

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_pack.py
python3 scripts/update_catalog.py --check
python3 scripts/build_release.py
```

Tests use synthetic fixtures. Catalog refresh checks the official public specification; inspect and test drift before installing it. The build creates a deterministic, checksummed distribution excluding provider-generated catalogs, credentials, caches, customer records and local configuration. Run catalog bootstrap again after extracting a release.

Mutating operations are local previews until `--execute` is supplied. The user's authorization remains required; the flag is not permission. GET retries are bounded, writes are never automatically retried, and ambiguous outcomes require reconciliation. See [security and action semantics](SECURITY.md).

## License and sources

Original code and instructions in the public distribution are licensed under [MIT](LICENSE). Separately supplied private workflow companions are not covered by that license unless their own notices say otherwise. Dialpad's specification and other third-party materials are separate; see [third-party notices](THIRD_PARTY_NOTICES.md).

[Official API collection](https://developers.dialpad.com/reference/download-api-specification) · [developer index](https://developers.dialpad.com/llms.txt) · [native MCP](https://developers.dialpad.com/docs/dialpad-mcp-server) · [Zapier Dialpad MCP](https://zapier.com/mcp/dialpad)
