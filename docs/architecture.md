# Architecture

This document is reserved for daemon and mod runtime architecture details. The normative source remains [the unified spec](../specs/unified_spec.md).

Initial ownership boundaries:

- `daemon/src/yomi_daemon/` owns orchestration, protocol handling, adapters, and storage.
- `mod/YomiLLMBridge/` owns live game integration and mod-side safety checks.
- `schemas/` owns the versioned transport contract.

## Daemon Runtime Config

`WU-004` adds a daemon-only runtime config layer alongside the wire-safe `config.v1.json`
snapshot.

- `schemas/daemon-config.v1.json` validates daemon JSON config files before any match starts.
- `daemon/src/yomi_daemon/config.py` normalizes that file into a typed `DaemonRuntimeConfig`.
- `DaemonRuntimeConfig.to_config_payload()` emits the narrower `ConfigPayload` used for
  handshake pinning and manifests.

The split is deliberate:

- Runtime config can include daemon concerns such as transport host/port, tournament defaults,
  and provider credential environment variable names.
- Wire config remains safe to share with the mod because it excludes credentials and keeps only
  match-relevant settings such as timeouts, fallback mode, logging, policy assignment, and
  character selection.

## Config Precedence

Daemon startup resolves config in this order:

1. Built-in defaults in `yomi_daemon.config` for transport, logging, timeout profiles, character
   selection, tournament defaults, and `trace_seed`.
2. A selected JSON config file, defaulting to
   `daemon/config/default_config.json`.
3. CLI overrides from `yomi-daemon --host/--port/--p1-policy/--p2-policy/--trace-seed`.
4. Environment lookup for provider credentials referenced by `credential_env_var`.

Environment variables only supply secret values. They never change structural settings such as
policy IDs or transport bindings.

## Manifest Foundation

`daemon/src/yomi_daemon/manifest.py` builds a serializable manifest skeleton before the first
decision turn.

The initial skeleton pins:

- daemon version
- accepted schema/protocol version
- trace seed
- effective stage ID
- policy mapping plus per-policy provider/model/prompt metadata
- the wire-safe config snapshot that was handed to the session

That gives later storage work units a stable manifest contract even for incomplete matches.
