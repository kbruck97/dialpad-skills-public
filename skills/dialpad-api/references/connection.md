# Connection and portability

The same skill files run in Codex, Hermes, or another agent with Python 3.10+ and shell access. Select the authorized account deliberately. Nothing in the repository selects an organization's credential or SSH host by default.

## Local execution

Use `DIALPAD_API_KEY` in the process environment, or a protected environment file containing that exact variable. Never paste a real key in chat or a command argument. File parsing reads the variable without evaluating shell code.

```text
python3 RUNNER --via local --env-file /protected/dialpad.env doctor --live
```

An explicit selected file takes precedence over an ambient key. Keep credentials outside source control; `.env.example` is a placeholder only. An existing OAuth access token uses the same Bearer mechanism, but OAuth issuance/refresh is separate; see `../../dialpad-integrations/references/connections.md`.

## Persistent configuration

By default use `$HERMES_HOME/.config/dialpad-skills/connection.json` when `HERMES_HOME` is set, otherwise `$XDG_CONFIG_HOME/dialpad-skills/connection.json` when set, otherwise `~/.config/dialpad-skills/connection.json`. Override with an explicit `--config /path/connection.json`, or `DIALPAD_CONFIG_FILE`. Configuration permits only `via`, `env_file`, `ssh_host`, and `remote_env_file`; it must not contain tokens. CLI options override environment configuration, which overrides file configuration.

Local example:

```json
{"via":"local","env_file":"/protected/dialpad.env"}
```

SSH example:

```json
{"via":"ssh","ssh_host":"authorized-dialpad-host","remote_env_file":"/protected/dialpad.env"}
```

Equivalent transport environment variables are `DIALPAD_ENV_FILE`, `DIALPAD_SSH_HOST`, and `DIALPAD_REMOTE_ENV_FILE`. A missing connection fails explicitly; there is no implicit fallthrough to a different host or tenant. A Hermes container needs its own accessible path, not the corresponding host path.

## SSH execution

```text
python3 RUNNER --via ssh --ssh-host authorized-dialpad-host --remote-env-file /protected/dialpad.env centers --name 'Company'
```

SSH sends the runner, catalog, arguments and optional action body over encrypted stdin; the remote process loads the key there. It does not copy the token back or put it in the SSH command. The action body is kept in the remote process memory rather than written to a temporary file. Timeout can leave an action outcome unknown; never resend blindly.

Requests use Bearer authentication to the fixed `https://dialpad.com/api/v2/` origin. Redirects are refused. Export/media URLs are separate provider-returned resources, never a reason to forward the Bearer header to another host. There is no arbitrary-origin, browser-cookie or private-web API mode.

## Troubleshooting

`doctor` checks the selected connection and reports only nonsecret status. SSH mode connects to the configured host even without `--live`; it makes no Dialpad API request until `--live` is supplied. Add `--live` for account connectivity. Connection configured, endpoint permitted and successful business workflow are different checks. 403 may mean missing scope/license, not a bad key. Avoid logging token or body contents when debugging. A remote helper does not configure a Windows browser, native MCP enrollment or Zapier connection.
