# Desktop observability pack

`observability-pack.yaml` records the exact catalog revisions reviewed on
2026-10-29. Inspect it with `hermes plugins pack show PATH`; install through
`hermes plugins pack install PATH`. Review newer revisions before updating.

| Plugin | Use |
|---|---|
| Loaded Skills Indicator | Loaded and compression-pruned skills; library browsing |
| Context Overview | Current context occupancy and estimated category breakdown |
| Session Lens | Session costs, tool failures, traces, and provider usage |
| Kanban Graph | Dependency graph and worker lineage for existing boards |
| Tailscale | Device inventory and remote Hermes connection selection |

These extend Hermes Desktop. They do not add panels to Codex or other relay
applications. Backend and Desktop installation roots can differ, especially
with Windows Desktop connected to a WSL or remote backend. Verify both halves
against the selected profile and connection.

Context Overview is frontend-only: the pinned package has a compiled
`desktop/plugin.js` and no Python `__init__.py`. It needs no Node build to run.
Do not treat a Python registration error as evidence that the Desktop entry
point failed. Leave its nonexistent agent half disabled, and check Desktop's
plugin discovery and enable toggle instead. The other four packages have Python
registrations; `hermes plugins doctor NAME --ci` checks those in isolation.

Materializing a Desktop package does not prove its renderer toggle is enabled.
Use Desktop's installed-plugin controls to enable it and verify its actual
status-bar contribution or panel. Existing user toggle decisions take precedence
over a package's defaults. Do not rewrite third-party code to force activation.

Session Lens may contact configured vendors' usage endpoints using existing
credentials. Tailscale connection actions are separate from installing the
browser. Installing this pack does not initiate model requests, connect to a
remote host, dispatch Kanban work, or change a profile's model.
