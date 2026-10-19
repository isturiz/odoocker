# Odoocker — plan and progress

Project tracking source. Moved from the conversation plan;
update decisions and results here. Keep implemented code, unit tests,
and live validation distinct.

## Goal and decisions

- Shared, versioned engine; declarative clients, not copied templates.
- Local macOS/Linux development, mainly for personal use; no production.
- `odoocker` is the project, `odx` the CLI, and `odx code opencode` the only
  supported assistant. `oc` can be an optional alias, never duplicated logic.
- Separate the OpenCode adapter from the core; do not implement Claude/Codex or
  a plugin framework ahead of actual needs.
- OpenCode retains native sessions, agents, and worktrees. Odoocker manages
  Odoo services and resources; it is not another Git manager.
- Do not modify Realista, global helpers, personal configuration, or their data.
- Never share mutable state between clients or delete volumes implicitly.
- Do not mount the Docker socket, SSH, or the entire personal profile in agents by default.
- Cloudpepper and Realista-specific logic will be added as later adapters.

## 0. Standalone project

- [x] Review the destination and instructions; create a new repository without commits.
- [x] Python 3.12 package with the `odx` CLI, uv, and an engine environment lock.
- [x] Create `AGENTS.md`, README, and this TODO within the project.
- [x] Local invocation without replacing global `odx`/`oc` commands.

## 1. Multi-version core

- [x] Separate validated manifest, private settings, and generated files.
- [x] Stable workspace UUID identity, explicit instances, and deterministic paths.
- [x] Generated Compose/Odoo configuration, private secrets, and dynamic localhost ports.
- [x] No published PostgreSQL port, `container_name`, or fixed global resources.
- [x] Review sources: Python 3.12 and PostgreSQL 16 are compatible with Odoo 17/19.
- [x] `init`, `fetch`, `configure`, `up`, `down`, `status`, `doctor`, `deps`, `run`.
- [x] `fetch` preserves existing checkouts; core is mounted read-only.
- [x] Editable local addons and per-repository/module requirements.
- [x] Unit tests for isolation, paths, errors, identities, and secrets.
- [x] Create separate 17 and 19 sandboxes, reusing only read-only core sources.
- [x] Validate live installation and startup of both versions simultaneously.
- [x] Reject stale generated configuration and instance reuse across versions.
- [ ] Include patched wkhtmltopdf, rtlcss, and an optional browser/tour profile.
- [x] Match the PostgreSQL client to the server (16/18 profiles, official PGDG packages).
- [ ] Make UID/GID configurable and validate editable mounts on a real Linux host.

### Stable local browser URLs

- [x] Optional shared native Caddy managed by `odx proxy start/stop/status`;
  explicit installation, private registry/logs/admin Unix socket, loopback HTTP only.
- [x] Stable `<workspace>-<instance>.localhost:8080` routes, explicit registration
  for existing instances, automatic refresh after CLI up/setup/OpenCode update,
  removal on down, and exact stale-route removal. Direct Docker URLs remain usable.
- [x] Automated tests: opt-in, validation, ownership collisions, locks, failed reload,
  persistent stop/start state, private profile, and status URLs.
- [x] Isolated live Caddy validation on macOS with a disposable HTTP backend:
  HTTP forwarding, Host/path preservation, unknown-host 404, WebSocket upgrade,
  route reload/removal, and shutdown. No existing workspace or system service touched.
- [ ] Live Odoo browser validation across down/up and simultaneous client instances;
  Linux native proxy validation. No claim of completed Docker/Odoo proxy validation.

## 2. OpenCode

- [x] Concrete adapter and `odx code opencode` command; no other assistants.
- [x] Linux binary pinned to an exact version, with separate host client and server.
- [x] Dedicated authentication; persistent configuration/data/state per instance.
- [x] Separate client TUI state per instance without touching its global configuration.
- [x] Agents can run `odx status`, `deps`, `run`, and `test` without the Docker socket.
- [x] Validate a live authenticated connection with `odx code opencode --info` in both sandboxes.
- [x] Verify that the API rejects unauthenticated requests with HTTP 401.
- [ ] Validate interactive conversations and provider authentication on the new server.
- [ ] Verify conversation persistence after recreation/rebuilding.
- [x] Verify empty session persistence after `down`/`up`; provider conversation still pending.
- [ ] Validate native worktree paths and metadata from the host and container.
- [ ] Associate resources with existing addons checkouts without managing their Git lifecycle.
- [ ] Explicit, limited export of global configuration without copying secrets.
- [ ] Evaluate safe automatic server startup from `code` after validating the basic workflow.

## 3. Tests and concurrent agents

- [x] `odx test`: database, filestore, and data per UUID; never targets `dev`.
- [x] Temporary container with no published ports from the host; coordinated port inside the agent.
- [x] Shared/exclusive environment lock and exclusive regular database lock.
- [x] Logs and JSON result per run; `--keep` for diagnostics.
- [x] Reject an empty test selection as a success.
- [x] Cleanup limited to the generated namespace; preserve artifacts and report errors.
- [x] Validate limited live smoke tests for Odoo 17 and 19.
- [x] Validate two concurrent tests inside OpenCode and preservation of the regular database.
- [x] Validate SIGINT, empty selection, and `deps` locking with Odoo active in real containers.
- [ ] Handle SIGTERM and explicit recovery of abandoned resources after forced shutdowns.
- [ ] Add active run discovery and block `down` during managed operations.

## 4. Reproducibility

- [ ] Lock Git revisions, image digests, and Linux dependencies per profile/platform.
- [ ] Separate `sync` (materialize the lock) from `update` (update deliberately).
- [x] Declarative addons repository management without switching branches/discarding editable work.
- [x] Additional workspace requirements with Odoo constraints and a direct fingerprint.
- [ ] Recursive fingerprinting of `-r`/`-c` includes.
- [ ] Validate rebuilding from a clean machine/workspace.
- [ ] Schema versioning and explicit state/path migration without blindly reassociating data.

## 5. Adapters and adoption

- [x] Initial trusted Odoo SQL ZIP restore with native module neutralization,
  explicit role policy, update checkpoints and resumable publication (details below).
- [ ] Cloudpepper adapter without version-controlled credentials.
- [ ] Adapt Realista-specific scripts.
- [x] Private OpenCode state backup before explicit updates.
- [ ] Opt-in Realista migration with backup and a return path to the previous workflow.

## Live validation log

- Test environment: macOS arm64, Docker Desktop; Realista 17/19 already running.
- Profile verified against local Odoo 17 and 19 sources; this does not mean tests were run.
- First Linux image and sandbox-19 services built/started successfully.
- Combined requirements installation and `uv pip check` successful: 65 packages
  in Odoo 17 and 66 in Odoo 19, in separate Linux volumes.
- `base` initialized and `/web/login` returned HTTP 200 in Odoo 17 and 19 simultaneously;
  smoke HTTP processes stopped afterward, development services preserved.
- Concurrent OpenCode 2.0.30 servers with distinct dynamic ports and authenticated
  API verified on both. Requests without a password rejected with 401.
- `/base:TestACL.test_field_visibility_restriction` smoke passed on both versions:
  one test per run, exit code 0, and database/filestore cleanup confirmed.
- Two parallel runs of that smoke inside the same OpenCode 19 container:
  both successful, distinct IDs/databases, no leftover test databases, and no changes
  to the login count/hash in the `dev` database.
- Selecting a nonexistent class returns exit code 1, zero tests, and cleans up its resources.
- SIGINT during test database initialization terminates the Odoo child, preserves
  the result/log, and deletes the run's database/filestore.
- `deps` rejected by a lock while `odx run` uses the environment, without mutating packages.
- Wheel built and invoked from an isolated uv environment without global installation.
- 317 unit tests passed; Ruff lint/format checks passed. The wheel includes Docker
  resources and was rebuilt with the final engine version.
- Parallel smoke repeated across Odoo 17 and 19 after rebuilding the final image:
  both runs successful with no leftover test data.
- Sandbox 17 `down`/`up` preserves its seven volumes, `dev` database, and an empty
  session created for the test; the temporary session is deleted afterward.
  The sandbox 19 OpenCode container remains untouched during this check.
- These do not replace a full suite: PDF, tours, and provider conversations remain pending.

### Delivery: declarative Realista lab configuration

- [x] Prepare the existing `realista-lab` workspace without reinitializing it.
- [x] Move all 21 repositories to `[[addons]]` in `odoocker.toml`, preserving
  URLs, branches, checkout names, and the original addons_path priority.
- [x] Move 44 additional dependencies from pyproject to workspace requirements
  (only the duplicate openupgradelib entry is removed).
- [x] Configure the original development limits without copying passwords,
  production/MCP configuration, or references to existing Realista databases.
- [x] Add `fetch --dry-run` and safe cloning of declared repositories to the engine.
- [x] Validate cloning/retries with temporary local Git repositories, preservation
  of working branches and uncommitted changes, wrong origins, unsafe destinations, and cleanup.
- [x] Compare the prepared manifest and requirements with the original project,
  and run a dry-run from the new workspace without networking or generated state.
- [x] 364 automated tests passed, Ruff lint/format checks passed, and wheel
  built/checked in an isolated environment. This delivery was not validated with Docker.
- [ ] Download private repositories and validate actual authentication/branches.
- [ ] Resolve/install all Realista dependencies and validate its modules on Linux.
- The 21 repositories were not cloned, dependencies were not installed, and Realista
  lab containers were not started in this delivery. Existing sandboxes are not recreated.

### Fix: Requests conflict in Realista lab

- [x] Identify incompatible requirements: core and workspace require 2.31.0 versus
  the Realista addons clone's 2.32.4 pin.
- [x] Adjust only the lab clone to `requests>=2.31.0,<3` and leave
  the workspace version under the core constraints' control.
- [x] Dry-run resolution of the full set in the Linux container: 229 compatible
  packages, selecting Requests 2.31.0. No overrides or changes to Odoo/the original project.
- [x] Verify live installation in the lab container: 229 packages,
  successful `uv pip check`, and Requests 2.31.0 under Python 3.12.13.
- [x] Odoo 19 `/base:TestACL.test_field_visibility_restriction` smoke passed
  with one test, exit code 0, and temporary database/filestore deletion.
- This metadata change does not demonstrate compatibility of all integrations
  or fix vulnerabilities in an old version pinned by core.

### Delivery: PostgreSQL 18 and pgvector

- [x] Add `[postgres]` with major version 16/18 and optional pgvector, keeping
  PostgreSQL 16 and its volumes for existing manifests without that section.
- [x] Separate the PG18 volume (`db-data-pg18`) from the original PG16 volume (`db-data`),
  mounted at `/var/lib/postgresql` for the PG18 layout, with no implicit migration/deletion.
- [x] Require `configure --allow-database-switch` consent when changing major versions,
  and reject the explicit switch while services are running.
- [x] Install a matching major-version client from signed PGDG packages and separate
  Odoo/OpenCode images by PostgreSQL version.
- [x] Configure Realista lab for PostgreSQL 18 and pgvector 0.8.7 with a fresh database,
  as authorized; preserve the existing PG16 volume, filestore, and OpenCode state.
- [x] 386 automated tests passed and Ruff checks passed.
- [x] Live validation in Realista lab (macOS arm64/Docker Desktop): server and
  `psql`/`pg_dump`/`pg_restore` clients at 18.6, pgvector 0.8.7, and directory
  `/var/lib/postgresql/18/docker` on the new volume.
- [x] Create the extension and query vectors in a temporary database; a custom-format
  dump using the Odoo container's client and restore into another database preserve
  the extension and rows. Both databases and the temporary dump are deleted afterward.
- [x] Initialize `dev` with Odoo 19, enable `vector` in it, and verify its UUID,
  extension, and test vector data after `down`/`up --code --no-build`.
  The temporary persistence database is deleted; `dev` is preserved.
- [x] Read `PG_VERSION = 16` in the previous volume through a read-only mount;
  it is neither deleted nor mounted as the PostgreSQL 18 server's data.
- [x] HTTP 200 at `/web/login`, `/base:TestACL.test_field_visibility_restriction` smoke
  passed (one test, zero errors/failures, test database and filestore deleted).
- [x] Wheel built; lab Odoo/OpenCode services left prepared,
  without leaving the smoke HTTP process running.
- [x] All 229 packages still pass `uv pip check`; recreated OpenCode returns
  authenticated HTTP 200. All six sandbox-17/19 containers retain their IDs and
  running state, without recreation or manifest changes.
- At that delivery, CLI-integrated restore/neutralization was not implemented;
  see the later integrated restore delivery below. Functional compatibility of
  all Realista modules is still not considered demonstrated.

## Outside the initial scope

Production, Kubernetes, graphical interface, distributed execution, agent
scheduler, custom worktree manager, public plugins, and assistants other than
OpenCode. Do not automatically migrate or clean up existing environments.

## Shared engine publication

- [x] Preserve the old `isturiz/odoocker` `main` on the remote
  `legacy/copied-template` branch, commit `7fc86a36fa6537b964dea9fcfc5431423cded276`.
- [x] Keep the previous history as ancestry of the new engine; do not create
  an orphan history or use force push.
- [x] Review publishable files: no private Realista configuration, generated
  state, credentials, or machine-specific paths. Add preventive gitignore exclusions.
- [x] Document the transition and correct the outdated PostgreSQL 15 client limitation.
- [x] Publish the replacement of the template with the engine through
  [PR #2](https://github.com/isturiz/odoocker/pull/2), preserving history.
- Do not modify client clones, running services, Fish functions, or global configuration.

## Local adoption of the installed CLI

- [x] After user authorization and their removal of the restriction in AGENTS.md,
  install `odoocker` with `uv tool install --python 3.12` from GitHub/main,
  initial revision `2026dd62fa7d6420b0352e6bda879f57fef6d395`.
- [x] Archive the old `odx`, `oc`, and `odooctl` Fish functions and `odx`/`odooctl`
  completions outside autoload without changing their contents; retain a checksum
  manifest and recovery instructions in the local archive.
- [x] Remove the `odooctl` alias instead of silently redirecting it to another CLI;
  leave `oc` as a minimal wrapper around `command odx code opencode $argv`.
- [x] Replace old completions with the new engine's commands/options.
  Keep `oc2`, `ocweb`, PATH, and native OpenCode configuration unchanged.
- [x] Validate in a fresh Fish session: `odx` resolves to an executable, version 0.1.0,
  correct help, no `odooctl` alias, and no old command suggestions.
- [x] Check Fish syntax, checksums of all five originals, and forwarding of
  arguments with spaces/exit codes using an isolated test executable.
- [x] `odx code opencode --info` from the installation connects to the existing
  Realista lab instance and verifies OpenCode 2.0.30 without recreating services.
- [x] Document user-local installation, coexistence with Fish, updates, and
  the optional `oc` shortcut without machine-specific paths.
- [x] Create a disposable workspace using the installed binary from Fish and
  verify `fetch --dry-run` without Docker or downloads; delete only that fixture.
- [x] 386 engine tests and Ruff lint/format checks passed; `oc --info` also works
  from Fish inside the existing lab. README/TODO updated without committing.
- Already-open terminals require opening a new one or unloading the old functions
  and completions from memory. Legacy commands without equivalents remain pending.

## Explicit OpenCode update

- [x] Add `odx code opencode update` to adopt the exact host version without
  updating the host or bringing back the global `oc update` subcommand.
- [x] Preserve TOML comments/configuration; reject layouts that cannot be safely
  edited and pending changes unrelated to OpenCode.
- [x] Workspace/instance locks and a maintenance container with Linux locks
  throughout the process; reject active sessions and managed Odoo operations.
- [x] Stop only the instance's Odoo/OpenCode services, back up all three native volumes
  and previous configuration with private permissions and a checksum, without the Docker
  socket or silently creating missing volumes.
- [x] Regenerate, build, recreate, and verify version/API; retain the backup and
  phased result without automatic downgrade/restore on failure.
- [x] Reject downgrades before stopping services (including prereleases); identify
  the previous container's actual image for retries after an incomplete build.
- [x] 422 automated tests passed, including failure transactions,
  data/configuration preservation, backup protocol, and actual lock retention.
- [x] Validate the live 2.0.30 -> 2.0.31 update in the project's own `update-smoke`
  workspace, authenticated API, preservation of session/title and synthetic pending
  inbox content (without invoking providers), and markers in all three volumes.
- [x] Check complete backup, SHA-256, and 0700/0600 permissions; PostgreSQL retains
  the same ID and is not recreated during the update; temporary guard removed.
- [x] Validate rejection with an occupied Odoo lock without stopping services or editing
  the manifest; injected build failure preserves the backup and leaves only PostgreSQL
  running, with locks released after stopping Odoo/OpenCode. Retrying normally works.
- [x] Validate retry when Compose points to a new image that does not exist,
  using the actual previous image by ID for the maintenance container.
- [x] Odoo 19 smoke passed (one test, zero errors/failures, temporary resources
  cleaned up), wheel built, and help run from an isolated installation.
- [x] Stop the validation workspace, preserving its volumes and backups;
  do not update Realista instances or existing sandboxes.
- [ ] Guided restoration of the OpenCode state backup after a failed update.
- The pin remains workspace-wide, while service updates are per instance.
  External processes and new clients require human coordination during maintenance.
- Distribution: publish the engine on GitHub/main and upgrade the CLI with
  `uv tool upgrade odoocker`, preserving its Git origin. Verify help and the
  installed revision without running the updater on any user workspace.
- CLI installation/upgrades and OpenCode server updates are independent operations;
  containers only change on explicit request.

## Simplified initial setup

- [x] Add `odx setup` and `odx setup --code`, retaining individual configure/up/deps
  and fetch commands; do not initialize/restore databases or change pins automatically.
- [x] Stage the build separately, acquire Linux environment/regular database leases,
  and hold them through configure/up/deps and optional OpenCode startup.
- [x] Reuse the existing maintenance protocol with the OpenCode updater,
  without duplicating process logic or adding an orchestration framework.
- [x] Skip deps when the current interpreter and marker/fingerprint are valid;
  keep `odx deps` as explicit reinstallation and retain include limitations.
- [x] Reject managed operations/active sessions without stopping them; preserve a
  previously running OpenCode even when setup is invoked without --code, and warn
  about differing host/container pins without updating them or blocking initial setup.
- [x] Automated tests for ordering, phase failures, first installation,
  concurrent configuration, locks, manifest preservation, and no database initialization.
- [x] 455 automated tests passed and Ruff checks passed, including updater regressions
  after sharing lease management and corrupt/non-object deps markers.
- [x] Live first installation with `setup --code` in the project's own `setup-smoke`
  workspace: Odoo 19, PostgreSQL 16, 66 compatible packages, and OpenCode 2.0.31
  with an authenticated API.
- [x] Repeating setup without --code preserves the previously running OpenCode and
  PostgreSQL ID; it does not reinstall packages (marker contents and nanosecond mtime
  unchanged), change the manifest, or create `dev`. Temporary build files removed afterward.
- [x] An occupied live lock rejects setup without altering container IDs or Compose.
- [x] An invalid test requirement stops deps before OpenCode startup, deletes the
  ready marker, and releases locks. Restoring only that fixture and repeating
  setup --code recovers the environment and passes uv pip check.
- [x] The updater with backups still works in that workspace after extracting shared
  utilities; Odoo 19 smoke passed with one test and resource cleanup.
- [x] Stop the validation workspace, preserving volumes/backups; no Realista or
  existing sandbox container is recreated in this delivery.
- [x] Build wheel/sdist and check `odx setup --help` from the wheel installed
  in an isolated environment.
- Explicit distribution: publish on GitHub/main and upgrade the CLI with
  `uv tool upgrade odoocker`, preserving the Git origin. Check the installed
  command's help without automatically setting up the client's services.
- `odx fetch` remains independent: it only clones missing sources and preserves
  existing repositories, branches, and changes; it is neither pull nor setup.

## English documentation

- [x] Translate README and TODO into English, including illustrative example names.
- [x] Preserve task status, historical validation results, safety warnings, and real references.
- [x] Verify links, code examples, and all 127 historical task statuses; 455 automated
  tests, Ruff lint, and Git whitespace checks passed. No remaining Spanish detected
  in tracked files.
- Documentation-only delivery; no new live Docker validation is required or claimed.

## One-off client restore validation

- [x] Restore a trusted client ZIP into an isolated PostgreSQL 18 staging database
  and filestore without changing the original archive. This uses private operation
  scripts, not a new installed `odx` command or shared-engine restore implementation.
- [x] Diagnose ten missing columns across five installed modules; upgrade those
  selected modules and Odoo's dependent modules in a second DB/filestore copy,
  retaining the first restored copy. Do not use `-u all` or change addon branches.
- [x] Repeat native module neutralization, regenerate copy identifiers and local
  administrator access, and verify 136,230 referenced attachment sizes/checksums.
  Publish `dev` only after schema, module state, and neutralization checks pass.
- [x] Fix a locally checked-out `mcp_server` hook to find inactive cleanup crons
  instead of duplicating them on startup. Its regression fails before the fix and
  all three targeted tests pass afterward; all 23 selected logging/cron tests pass
  in fresh, managed test databases, with automatic DB/filestore cleanup.
- [x] Live login page and authenticated web client return HTTP 200. After the
  normal server starts, confirm zero active crons, real outgoing mail servers,
  incoming mail servers, and pending module operations. Preserve private access
  details, logs, and recovery copies; do not publish client secrets or backup data.
- The initial addon fix was kept local until separately authorized publication.
  This validation is not a full client
  functional, browser, PDF, or external integration review, nor anonymization.
- [x] Integrate an initial trusted-ZIP restore workflow separately, with explicit role/ACL
  policy, resumability, recovery checkpoints, and per-version neutralization. The
  one-off text-based role inventory is not a complete SQL parser or a public API.

## Integrated development restore and capability compatibility

- [x] Add host-only `odx db restore <backup.zip>` and `--resume <id>`, without
  importing private client scripts or names into the core. Keep existing workspaces
  and the installed/global CLI unchanged unless explicitly prepared/upgraded.
- [x] Validate ZIP paths/types/CRC and source SHA-256; import SQL transactionally
  with a completion receipt. Keep DB/filestore checkpoints and prior destination
  identities; reject collisions and reconcile interrupted publication on resume.
- [x] Reject an existing `dev` without `--replace`. Acquire host/Linux leases,
  reject active managed operations/conversations and use offline PostgreSQL/worker
  containers with a Unix socket and explicitly scoped Compose/volume names.
- [x] Default to diagnosing missing roles. Allow restricted NOLOGIN creation only
  with `--create-missing-roles`; preserve existing roles and record creation attempts.
  Parse pg_dump lexical boundaries, including quoted identifiers, comments, dollar
  bodies and COPY rows; reject unsupported client/cluster/transaction operations.
- [x] Keep copied administrator credentials unless `--reset-admin-password` requests
  password `admin` for `base.user_admin`, without changing its login, privileges or MFA.
  Support exact installed-module updates and `--update-outdated`, including newer
  local manifests and same-version missing-column owners, with no `-u all`/downgrades.
- [x] Run native module neutralization before hooks and after updates/preparation;
  reject reactivated cron/mail, pending modules, missing schema and invalid attachments.
  Publish only after verification, regenerate copy identifiers and refresh the local URL.
- [x] Replace Odoo-release branches with capability checks for config/logging,
  demo-option semantics and a structurally inspected registration-hook boundary.
  Fail closed on unknown capabilities; do not catch-and-retry internal TypeError.
- [x] Accept stable `MAJOR.0` version strings independently from the informational
  live-validated list (17.0/19.0). Warn about unvalidated versions without corrupting
  JSON stdout; inspect literal minimum-Python metadata without executing source code.
  Do not claim arbitrary releases work with the current Python/image/dependencies.
- [x] Live dedicated Odoo 19/PostgreSQL 18 workspace: native synthetic ZIP, missing
  role default rejection and explicit creation, transaction retry without re-extraction,
  same-version/newer-version fixture addon updates, password reset and preservation,
  retained replacement/update copies, 11 attachment checks and zero active cron jobs.
- [x] Live dedicated Odoo 17/PostgreSQL 16 workspace: native synthetic ZIP restore,
  neutralization, explicit base update, password reset and 56 attachment checks.
  Repeat both releases after replacing all release-based restore branches.
- [x] Live busy-lease rejection preserves normal container IDs. Only the new restore
  validation workspaces were addressed; Realista and existing sandboxes were not changed.
- [x] 569 automated tests pass, including capability shapes with no release-number
  dispatch, invalid/unknown metadata, role/ZIP boundaries, explicit update policy,
  host/Linux busy rejection and interrupted import/publication recovery.
- [x] Verify final password reset, zero active crons/pending modules, unchanged input
  archive hashes and completed-resume idempotence in both dedicated workspaces.
  Stop their normal services afterward, preserving volumes, backups and checkpoints.
- [x] Ruff lint/format, Git whitespace checks and source/wheel builds pass. Install
  the wheel in an isolated environment and verify the packaged restore modules and
  CLI options. Distribution is explicit: publishing the engine does not upgrade
  the installed/global CLI or recreate client containers automatically.
- [ ] Integrated backup command, arbitrary/custom dump formats, cleanup of legacy/previous
  recovery pairs, role mapping/privilege migration and full client functional/PDF/browser tests.

## Clean-volume client restore follow-up

- [x] Explicitly reset only the selected client's four data/environment volumes,
  retaining Git/source state, workspace identity/configuration, original backup and
  the three OpenCode volumes. Verify the backup's complete CRC and SHA-256 before
  removal and verify retained OpenCode content hashes after cleanup.
- [x] Recreate the runtime through the installed `setup --code`, install all 229
  dependencies into a new virtualenv and keep `dev` absent before restoration.
- [x] Reproduce a first-restore failure when the fresh data volume has no filestore
  parent. Create that parent privately before the atomic move; reject symlink or
  non-directory roots. Cover both first import and resume without another SQL import.
- [x] Preserve database-imported data modules using native `imported` metadata, not
  module-name exceptions. Exclude them from Python-manifest comparisons/updates,
  continue rejecting unavailable Python addons and respect the native loadable-module
  domain during registry validation. Cover unknown/absent metadata capabilities.
- [x] Resume the same real restore operation with the engine checkout after both
  fixes: select 15 outdated/schema-owner modules, preserve the database-only data
  module and verify 136,230 referenced attachments without reimporting the SQL.
- [x] Confirm local administrator password `admin`, login and authenticated web
  client HTTP 200, healthy OpenCode API, zero active cron/real mail servers and
  pending module operations. Original ZIP SHA-256 and all 22 workspace/addon Git
  states remain unchanged. Full business/integration/PDF validation is still pending.
- [x] 580 automated tests, Ruff lint/format, whitespace checks and wheel/sdist builds
  pass. Preserve private logs, SQL and recovery checkpoints for this live validation.
- This follow-up resumed from the checkout. Host CLI distribution is included in
  the later maintenance-command delivery below; client container updates remain explicit.

## Disposable restore SQL retention

- [x] Delete only the extracted `dump.sql` after verified publication and successful
  host finalization. Keep SQL on failed/incomplete restores; add explicit `--keep-sql`
  retention, persisted across retries. Never delete the source ZIP or recovery pairs.
- [x] Validate operation identity/completion and extraction receipt; refuse symlink
  scratch paths or non-regular SQL files. Keep small metadata and logs untouched.
- [x] Record cleanup results; warn rather than fail a published restore if disposal
  fails. Completed `--resume` retries pending cleanup without stopping services or
  reimporting SQL, including explicitly resumed older completed operations.
- [x] 604 automated tests pass, including scoped deletion, failure/retention,
  finalization ordering, unsafe paths, legacy records and cleanup-only retries.
- [x] Ruff lint/format and whitespace checks pass; build wheel/sdist and verify
  `--keep-sql` help and the packaged cleanup script from an isolated wheel install.
- [ ] Live Docker validation of automatic SQL disposal after a new restore. Existing
  client SQL and DB/filestore checkpoints were not deleted in this delivery.
- Host CLI distribution is included in the maintenance-command delivery below;
  installing the host package does not refresh existing client containers.

## Instance database maintenance and service terminals

- [x] Add host/container `db list` with PostgreSQL sizes, connection counts,
  protected database flags, development/test/recovery classification and JSON output.
- [x] Add exact-name `db drop` with dry-run, typed confirmation or explicit `--yes`;
  no patterns, bulk selectors, forced connection termination or volume removal.
  Recheck database/filestore identities after confirmation and protect templates,
  foreign-owned databases, active connections and unfinished restore checkpoints.
- [x] Delete the selected pair only, including retained tests' isolated filestores.
  Keep drop audit records private, quarantine before filesystem deletion and report
  partial failures without deleting orphan filestores or silently retrying.
- [x] Add `passwd` targeting `dev`, administrator XML ID or exact `--login`, secure
  confirmed terminal input and explicit `--password-stdin`. Use native hashing,
  verify before commit and preserve login/activation/permissions/MFA. Reject pending
  module operations and incompatible/missing code, without release-number branches.
- [x] Add host-only `sh odoo|pg|opencode` and `sh pg --psql`, no implicit target,
  root login, automatic service startup or Docker socket in agents. Preserve TTY,
  selected instance, exit codes and maintenance/environment leases.
- [x] Live dedicated Odoo 17/19 workspaces on PostgreSQL 16: passwords authenticate over HTTP,
  renamed/inactive account fields remain intact, exact DB/filestore drops and retained
  test cleanup pass, protected/connected targets are refused without terminating the
  client, and all three service shells plus `psql` work. OpenCode executes inventory
  without Docker; shell and active-server locks block destructive/dependency work.
- [x] Keep live logs/results in those validation workspaces; do not change the client
  workspace, original backup, global CLI or existing sandbox runtimes.
- [x] Live PTY validation: hidden, confirmed password entry without echo and exact
  typed drop confirmation. 707 automated tests pass, covering dispatch, leases,
  unsafe paths, identity changes, partial deletion, password inputs and native APIs.
- [x] Ruff lint/format and whitespace checks, wheel/sdist builds and isolated wheel
  command-help/worker imports pass. Stop both new validation workspaces, preserving
  their volumes, development data and private audit/log records.
- Distribution for this delivery: publish on `main` and explicitly upgrade the host
  CLI from its existing Git origin; verify the installed revision and command help.
- [ ] Deliberately prepare updated client containers after the host CLI upgrade.

## Successful restore checkpoint retention

- [x] Dispose of new restores' pre-update database/filestore checkpoints by default,
  only after verified publication and successful host finalization. Add explicit
  `--keep-checkpoints`, independently of `--keep-sql`, and retain both policies on retry.
- [x] Keep failed/incomplete restore checkpoints, original ZIPs, `dev`, previous
  destinations preserved by `--replace`, logs and other operations' resources.
  Legacy operations retain their existing checkpoint policy, including on resume.
- [x] Record the import checkpoint's native database OID and filestore inode before
  copying/updating. Reject mismatched/overlapping identities, unsafe paths, foreign-owned
  or connected databases and busy native environment/database leases; never FORCE drops.
- [x] Journal cleanup separately from host summaries in private `checkpoint-cleanup.json`.
  Reconcile interrupted drops, quarantines and partial filestore deletion on completed
  `--resume`, without reimporting, overwriting an active worker's journal or stopping services.
  Warn on housekeeping failure without marking the published restore as failed.
- [x] 754 automated tests pass, covering default/explicit/legacy retention, finalization
  ordering, identity and symlink guards, native leases and cleanup interruption/retry.
- [x] Ruff lint/format and whitespace checks; source/wheel builds and packaged CLI
  help/cleanup script checks pass.
- [ ] Live Docker validation of new default checkpoint disposal and explicit retention.
- No client services/data, old recovery pairs, original ZIPs or installed/global CLI
  were changed. Adoption/publication remains explicit.

## Configurable Odoo web database manager

- [x] Accept strictly typed `odoo.options.list_db` and validated single-line
  `dbfilter` expressions; retain disabled-by-default and `^dev$` behavior.
- [x] Generate an independent private master password on first enablement, preserve
  it per instance across regeneration/toggles, and keep existing credentials intact.
- [x] Preserve the primary `dev` target, managed connection/paths and localhost-only
  application ports. Document explicit adoption and browser operations' lack of
  maintenance leases, checkpoint protection, restore verification and neutralization.
- [x] Accept the manifest's HTTP filter in restore topology checks without weakening
  the fixed restore destination or accepting undeclared generated configuration changes.
- [x] 806 automated tests pass (`uv run pytest`); Ruff lint/format and whitespace
  checks pass. Coverage includes option types/injection, legacy credentials, secret
  stability/isolation, disabled defaults and restores with configured HTTP filters.
- [ ] Live Docker validation of enabled manager and HTTP selection of development copies.
- No installed/global engine update, client configuration changes, service operations
  or database changes are part of this delivery; adoption remains user-managed.
