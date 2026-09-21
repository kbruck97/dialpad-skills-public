# Version 1.1.0 verification

This release separates reusable tooling from local account configuration and organization-specific workflows. Tests use synthetic fixtures and must not send customer messages or change live account configuration.

## Initial candidate checks

On 2026-09-21, the clean source export passed 91 tests on Python 3.14.4 after downloading the official catalog. The catalog contained 241 REST operations and three separately handled OAuth endpoints; its checksum matched the provenance recorded in the third-party notice. All seven public skill entrypoints passed metadata validation.

A fresh release ZIP passed extraction, catalog generation, installation and rollback checks. Updating public skills preserved a separately installed companion skill. The ZIP contained neither provider catalog files nor companion instructions. Missing-catalog installation failed before replacing an existing runtime.

These are local checks. Hosted CI and live provider actions require their own recorded results.

## Reproduce checks

From the repository root, generate the local provider catalog and run:

```sh
python3 scripts/update_catalog.py
python3 -m unittest discover -s tests -v
python3 scripts/validate_pack.py
python3 scripts/update_catalog.py --check
python3 scripts/build_release.py
```

The catalog bootstrap and drift check read the official public specification. Record the tested revision, Python version, test result and catalog checksum when reporting a validation result. An archive must omit provider-generated catalogs; generate them locally before installing an extracted release.

## What tests can establish

- Request construction and transport behavior with synthetic inputs.
- Schema validation, credential selection, redaction, SSH transport, retry limits, pagination, call scoping, transcript completeness and CSV handling.
- Installation previews, backups, preservation of unrelated skills, drift and tamper refusal, symlink boundaries, rollback, interrupted-install recovery and deterministic archive verification.
- Skill metadata, local resource links, source hygiene and distribution boundaries.

These checks do not establish provider business-rule acceptance, current account permissions, an actual customer outcome, or live mutation safety for a particular request. Native MCP, Zapier and OAuth enrollment are separate host connections. Consult the exact revision's Actions result for hosted CI status; a configured workflow alone is not a passing run.
