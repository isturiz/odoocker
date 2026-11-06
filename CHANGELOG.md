# Changelog

Version-specific changes and compatibility notes live here. See [README.md](README.md)
for current usage and limitations.

## 0.2.0 — Unreleased

### Added

- Restore additional named development databases with `odx db restore --database NAME`,
  retaining `dev` as the default and preserving per-destination replacement/recovery.
- Serve finalized named restores through Odoo's native multibase server, with one
  PostgreSQL, one Odoo process, and one OpenCode container per engine instance.
- Access any explicit development database through `odx shell --database NAME`,
  including scripts from stdin, using the native Odoo ORM shell in Linux.
- Select the target for module initialization/upgrades and password changes with
  `--database`. Module operations never receive the server's multibase list.
- Show browser links in host status and register stable per-database proxy aliases
  to isolate browser cookies without creating additional Odoo processes.

### Compatibility

- Existing manifests and dev-only instances require no data migration. Rebuild
  the selected containers deliberately to install the updated in-container engine.
  Odoo, PostgreSQL and OpenCode pins are unchanged.
- Old restore journals without a database target still resume into `dev`; a resume
  cannot change its original destination. Existing recovery copies are not enrolled
  as development databases or automatically removed.
- Named targets become available to the next `odx run` after successful host
  finalization. Their exact names augment the configured HTTP filter. Explicit
  manager filters retain their existing behavior; avoid broad filters exposing
  recovery/test databases. With `list_db = false`, use the links from `odx status`.
- All development databases share Odoo/addon code and dependencies. Restoring one
  still requires an instance-wide maintenance pause. Normal HTTP and ORM shells
  can coexist; module/password maintenance requires the applicable database lease.

## 0.1.1

### Changed

- Start the shared local Caddy proxy and register routes automatically during
  `odx setup`, `odx up`, `odx run`, successful restores, and successful OpenCode
  updates. OpenCode connections also start the proxy and refresh their own route.
- Use the stable browser URL for `web.base.url` and `access.json` after a
  successful restore when the proxy is available; preserve the internal
  `report.url` used for PDF reports.
- Fall back to direct access with a warning when proxy startup or route refresh
  fails, without invalidating a successful Docker operation or restore.

### Compatibility

- Proxy startup is automatic rather than opt-in. Existing route registries and
  their configured ports remain supported without manual migration.
- `odx proxy stop` now persistently disables automatic startup for all workspaces
  on the host, retaining routes. `odx proxy start` explicitly re-enables it.
- Caddy must still be installed separately. No OS startup service is installed;
  after a host reboot, the next applicable `odx` command starts the proxy.
