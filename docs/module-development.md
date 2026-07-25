# Module Development

Add one package under `src/personal_mcp_gateway/modules`, one YAML manifest, and adapter-specific
tests. Do not change gateway core to add Foxlink or Journal.

Implement `GatewayModule` with immutable identity, `start`, `stop`, `health`, `tools`, and
`resources`. Tool names must use `<module>_<verb>_<object>`, have precise typed parameters, return
the common envelope, declare permissions, and be deterministic in the registry.

Read operations may return source data but must not copy it into SQLite. Reversible writes require
`requestId` and `expectedRevision`; control commands additionally require `commandId`,
`expectedState`, and `expiresAt`. Permanent deletion is not exposed in v1.

Module manifests accept `http`, `subprocess`, and `in_process`; only HTTP execution is enabled in
v1. Unsupported modes fail that module without stopping the gateway.
