# Architecture

This document is reserved for daemon and mod runtime architecture details. The normative source remains [the unified spec](../specs/unified_spec.md).

Initial ownership boundaries:

- `daemon/src/yomi_daemon/` owns orchestration, protocol handling, adapters, and storage.
- `mod/YomiLLMBridge/` owns live game integration and mod-side safety checks.
- `schemas/` owns the versioned transport contract.
