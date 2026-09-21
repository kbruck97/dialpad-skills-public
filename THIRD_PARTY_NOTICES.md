# Third-party materials

The [MIT license](LICENSE) covers the original code and instructions in this public distribution. Separately supplied private workflow companions have their own notices. It does not relicense Dialpad's API specification, documentation, product names, or other third-party materials.

## Dialpad API specification

The catalog bootstrap reads Dialpad's official OpenAPI collection:

- Specification: <https://dialpad.com/static/openapi/platform-v1.0.json>
- Official download reference: <https://developers.dialpad.com/reference/download-api-specification>
- Developer documentation: <https://developers.dialpad.com/>

The specification checked on 2026-09-21 had SHA-256 `ff86ce21f8e25eb5361379f8af8b6dd81cb27f2588ebfcd49927070a892dac23`, with 241 standard REST operations and three OAuth endpoints. Each generated catalog records its actual source URL, checksum, source version, generation date and coverage; later downloads may differ.

Provider-derived catalog files are generated locally for use by the runner. They are excluded from this project's public source and release archives. The project makes no claim that those provider materials are covered by its MIT license. Refer to Dialpad's published terms and any applicable agreement for use of its materials and services.

## External services

References to Dialpad, Codex, Hermes and Zapier identify compatible products or optional connections. This project does not grant access to those services or their account features, and makes no claim of endorsement by their providers.
