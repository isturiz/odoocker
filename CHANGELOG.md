# Changelog

Version-specific changes and compatibility notes live here. See [README.md](README.md)
for current usage and limitations.

## 0.1.1 — Unreleased

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
