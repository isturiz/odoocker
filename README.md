# odoocker

Shared engine for **containerized Odoo development**. Each client declares
its workspace instead of copying the engine's Dockerfile, Compose file, or scripts.

Standalone prototype: it does not install global shell functions, modify Realista,
or reuse its data. **The only supported assistant is OpenCode V2**, through
`odx code opencode`. See [TODO.md](TODO.md) for pending work and validation records.

### Transition from the previous template

`main` now contains the shared engine, not a template to copy for each client.
The previous version is preserved on the
[`legacy/copied-template`](https://github.com/isturiz/odoocker/tree/legacy/copied-template)
branch, and its history remains part of this repository. This change does not
automatically migrate or modify workspaces or containers created with that version.
Private client configurations and data are not part of the engine.

## Requirements

- macOS or Linux, `uv`, Docker with Compose, and Git.
- OpenCode V2 on the host. Each workspace pins an exact server version;
  the client must match. The host is not updated automatically.
- Internet access for images, source code, and dependencies.

The initial profile uses Python 3.12 and PostgreSQL 16; PostgreSQL 18 and pgvector
can be selected per workspace. Each instance has its own Linux virtualenv. Base
image versions are pinned by tag; digest pinning and source/dependency locking
are still pending.

Odoo versions use the stable `MAJOR.0` format. **17.0 and 19.0 are live-validated,
not an acceptance whitelist.** Other versions are accepted with a warning, not a
promise of compatibility: their Python, dependencies, addons and exposed APIs must
work with the selected runtime. Literal minimum-Python metadata is checked without
executing checkout code; native Odoo/dependency checks still apply when metadata
is absent. The current Python image is not changed automatically to accommodate a
different Odoo. Restore compatibility follows inspected capabilities, not release
number branches; unknown or ambiguous capabilities fail rather than being guessed.

## Install the CLI

```sh
uv tool install --python 3.12 'git+https://github.com/isturiz/odoocker.git@main'
odx --version
odx --help
```

The installation is user-local and independent of the engine checkout and
workspaces; there is no need to copy scripts per client or use `uv run` daily.
If the tools directory is not on PATH, run `uv tool update-shell` and open a new
terminal. Installing the CLI does not start or rebuild containers.

If a Fish function named `odx` already exists, it takes precedence over the executable.
Explicitly archive the previous helper and its completions outside autoload paths
before adopting the new command. `functions --erase odx` only unloads the function
from the current session: it does not remove its file. In the meantime,
`command odx` runs the binary without going through the function.

Also review aliases such as `odooctl` and wrappers such as `oc`: old helper syntax
is not automatically translated. Restores now use the explicit `odx db restore`
workflow below; `oc sync`/`update`/`pair` are not restored as aliases. An optional
shortcut with no duplicated logic is:

```fish
function oc --wraps 'odx code opencode'
    command odx code opencode $argv
end
```

To update an installation tracking `main`, use `uv tool upgrade odoocker`.
Then explicitly apply the changes to each workspace with `down`,
`configure`, and `up` (add `--code` if you use OpenCode), with no active operations.
This preserves volumes but does not migrate data between PostgreSQL versions.
You can also install a specific revision using `@<commit>` instead of `@main`.

### Update OpenCode for an instance

First update OpenCode on the host using your usual installation method.
Then, from the workspace, with no active conversations or operations:

```sh
opencode --version
odx code opencode update
```

The command adopts **the exact host version**: it does not look up `latest`, update
the host, copy its personal profile, or switch providers. It requires an already
prepared instance with its existing OpenCode volumes. It accepts no additional
update flags; `odx code opencode -- update` still forwards `update` to the TUI client.
If the host is older than the configured server, it rejects the downgrade before
stopping services; this check includes prerelease version precedence.

The workflow validates the manifest, rejects unrelated pending changes, and checks
for active foreground OpenCode sessions through its API. A maintenance container
keeps `run`, tests, and `deps` locked throughout the update. The instance's Odoo and
OpenCode services are stopped (they share an image), but PostgreSQL and other
instances are not. With OpenCode stopped, its configuration, data, and state volumes
are backed up; only then is `[opencode].version` changed, the image rebuilt, both
services recreated, and the version and authenticated API checked. `odx run` is not run.

Backups remain in:

```text
.odoocker/instances/<instance>/opencode-updates/<id>/
  opencode-state.tar.gz
  odoocker.toml
  compose.json
  context.json
  odoo.conf
  secrets.json
  result.json
```

They contain sensitive data (sessions, credentials, and configuration). The directory
is private, and `.odoocker/` must remain excluded from Git; do not publish these files.
`result.json` records the phase, outcome, previous image, and archive SHA-256 when
the backup completed. `compose.env` and the maintenance container's Compose file
are also retained for diagnostics. Backups are not deleted automatically.

On failure, the phase and recovery path are reported. If shutdown had already
started, the workflow attempts to leave Odoo/OpenCode stopped **before** releasing
the locks. No downgrade or automatic data restore is attempted: a newer version
may have migrated the state. The manifest may retain the new version if that phase
was reached; the previous one is preserved in the backup. Recovery requires stopping
the instance and restoring a consistent set of all three volumes, generated
configuration, and the previous version; do not start an old image against migrated
state. Automatic OpenCode state restoration is not implemented.
Backups reflect the start of each attempt, not necessarily the last working state:
take particular care to retain the first backup of a version change.
Retries after a failed build use the previous container's actual image by ID,
even if the generated Compose file already points to a new image that does not exist.

Locks cover `odx` operations, not arbitrary agent processes or external Docker
commands. Close clients and avoid new conversations during maintenance; session
checks are not a transactional barrier against other clients. SIGKILL or a Docker
crash may require manual diagnosis of the maintenance container before retrying.

For another instance, use `odx --instance <name> code opencode update`. The version
pin is shared across the workspace: only the selected instance is recreated; the
others must be updated explicitly before reconnecting with the newer host.
Even with the same version, the command backs up and rebuilds so you can apply
a new engine version or retry a failed update.

## Try the checkout without replacing your current `odx`

From this repository:

```sh
uv sync --group dev
uv run odx --help

uv run odx init ../workspaces/my-client --odoo 19.0
uv run odx --workspace ../workspaces/my-client fetch
uv run odx --workspace ../workspaces/my-client setup --code
uv run odx --workspace ../workspaces/my-client run --init base --stop-after-init
uv run odx --workspace ../workspaces/my-client code opencode --info
uv run odx --workspace ../workspaces/my-client code opencode
```

In another terminal, `run` keeps Odoo in the foreground:

```sh
uv run odx --workspace ../workspaces/my-client run
```

`status` shows the ports **actually assigned by Docker**. `up` prepares the
containers; Odoo only starts listening for HTTP with `run`. The services do not use
fixed host ports 8069/4097/5432.

From the workspace, you can also invoke the engine without installing it globally:

```sh
uv run --project /path/to/odoocker odx status
```

There is no need to install the tool or replace `odx.fish`/`oc.fish` just to try
the checkout. The `uv run` workflow remains useful when developing the engine.

### Initial setup in one command

After defining the client's configuration and repositories:

```sh
odx fetch
odx setup --code
```

`setup` coordinates **configure -> up -> deps**, and `--code` adds OpenCode startup
after preparing dependencies. Without the flag, it only prepares Odoo and
PostgreSQL; if OpenCode was already running, it checks that it is idle, stops it
during setup, and starts it again when finished. The individual commands
remain available for diagnostics and specific operations.

The command does not clone sources (that is still `fetch`), run Odoo, install
modules, create the `dev` database, restore data, update repositories, or change
manifest versions. For a new database, make that decision explicitly:

```sh
odx run --init base --stop-after-init
```

You can repeat `setup`: it uses Docker's cache and skips Python installation
when the interpreter and dependency marker match the current requirements
fingerprint. This is neither a reproducible lock nor a check of every package;
to reinstall deliberately, use `odx deps`. Changes to indirect `-r`/`-c` includes
still require that explicit command.

The build is prepared in a temporary directory without replacing files mounted
by the services. Before regenerating configuration, changing services, or touching
packages, Linux locks for the environment and regular database are acquired and
held until completion. If a managed `run`, test, or `deps` operation is active,
setup is rejected without stopping it: image layers may have been built, but the
active configuration has not been replaced and the virtualenv has not changed.

An active foreground OpenCode session also blocks the operation. As with the
updater, close clients and avoid external processes during maintenance:
`odx` locks do not coordinate external Docker commands or all agent processes.
If a phase fails, the workflow does not continue to later phases or delete volumes;
a stopped OpenCode service may remain stopped, and a failed installation invalidates
its marker. Fix the cause and repeat `setup --code`.

`setup --code` respects the pinned version even if it does not match the host:
it prepares the server and warns that you must run `odx code opencode update`
before connecting when the host is newer. If the host is older, it tells you to
update it, not downgrade the container. This also allows an initial installation
to complete with an older pin.
If the versions match, it checks the authenticated API at the end. A manual pin
change for an already generated instance is rejected: stateful updates must use
the command with backups, not `setup`. PostgreSQL/Odoo major version changes
also still require explicit isolation decisions.

### Reuse local sources without modifying them

```sh
uv run odx init ../workspaces/my-client \
  --odoo 17.0 --source /path/to/odoo-17
```

Core sources are mounted **read-only**. `fetch` clones them if missing and preserves
any existing checkout: it does not pull, reset, switch branches, or install
dependencies on the host. Automatic updates and commit locking are not available yet.

## Configuration

Version-controlled `odoocker.toml` file:

```toml
schema = 1

[workspace]
name = "my-client"

[odoo]
version = "19.0"
requirements = ["requirements.txt"] # Optional workspace-level Python dependencies

[odoo.options]
limit_time_real = 0

[opencode]
version = "2.0.30"

[postgres]
version = "18" # Optional: "16" (default) or "18"
pgvector = true # Optional, default false

[[addons]]
path = "addons/oca/partner-contact"
url = "https://github.com/OCA/partner-contact.git"
branch = "19.0"
```

`[[addons]]` replaces the manual repository/path list. `odx fetch` clones any
missing declared repositories, and their paths are automatically added to
`addons_path`. `odx fetch --dry-run` shows the plan without cloning or creating state.
Each destination must be inside the workspace's `addons/` directory, with no
duplicates, overlaps, or symlink escapes.

Existing checkouts are preserved **without pulling, resetting, or switching branches**,
even if they have uncommitted changes or are on a working branch. They are checked
to ensure they are standalone repositories with the declared origin; existing
directories are not replaced. A failed download stops the process and allows a
retry while preserving completed work. Private repositories use the host's Git
authentication, not keys mounted in containers. `fetch` is not a branch/worktree
manager or an update operation.

`odoo.requirements` lists workspace-relative files. `deps` resolves their
dependencies together with those of core and addons, using Odoo's requirements
as constraints. A declared file must exist; missing files are not ignored.
`odoo.options` only accepts memory, time, and request limits; it does not allow
overriding the PostgreSQL connection, ports, credentials, or managed paths.

Private `odoocker.local.toml` file:

```toml
[odoo]
source = "/local/path/to/odoo"
addons = ["./addons/my-client", "/path/to/oca-repository"]
```

Local paths are optional and are added to the declared repositories. There is no
need to repeat `[[addons]]` here; duplicate paths are rejected.

Each addons entry is a directory whose children are modules. They are mounted
read/write for development. Relative paths are resolved from the workspace, not
from the terminal. SSH keys, the Docker socket, and global OpenCode configuration
are not mounted. Project files such as `.opencode/` are part of the mounted
workspace and can be read by the agent.

The CLI rejects unknown keys to catch configuration errors early.
After changing the manifest, paths, or engine version, run `configure` and `up`
to regenerate/recreate. Do not regenerate while another session is using the instance.

### PostgreSQL and pgvector

`[postgres]` selects the major version of both the server and the command-line
client in Odoo/OpenCode (`pg_dump`, `pg_restore`, `psql`, etc.). The client is
installed from the official PGDG repository with signature verification. The server
is not installed in the agent container, and PostgreSQL's port is not published on the host.

`pgvector = true` uses the `pgvector/pgvector:0.8.7-pg<version>-bookworm` image.
The extension is **available**, not automatically enabled in every database:
each database that needs it must run `CREATE EXTENSION IF NOT EXISTS vector`,
either from its module or during a restore. `template1` is not modified.

Existing workspaces without this section continue using PostgreSQL 16 with their
original `db-data` volume. PostgreSQL 18 uses **a different volume**, `db-data-pg18`,
mounted at `/var/lib/postgresql`, following that version's layout. A PostgreSQL 18
server is never connected to a PostgreSQL 16 server's data directory.

To deliberately switch versions, first change the manifest and run:

```sh
odx down
odx configure --allow-database-switch
odx up
```

This workflow **does not migrate, restore, or delete data**. When using a version
for the first time, its volume will be empty; initialize `dev` with
`odx run --init base --stop-after-init` or use a validated restore workflow.
Switching back to a previous version reuses that version's volume: the flag does
not guarantee a fresh database every time. The filestore, Python environment, and
OpenCode state are preserved; the flag is not a reset.
For an existing development backup, use `odx db restore` as described below.

`.odoocker/` contains local identity, secrets, and generated files; **do not copy,
version, or delete it to clean an environment**: losing its identity leaves previous
volumes unassociated. Moving a stateful workspace requires a future migration feature.
PostgreSQL and OpenCode passwords are private and local to each instance.

## Restore a development backup

From a prepared workspace, **on the host**, restore a trusted native Odoo ZIP:

```sh
odx db restore /path/to/backup.zip
```

The ZIP must contain plain `dump.sql` and, when attachments are present, its native
`filestore/`. An optional Odoo `manifest.json` is accepted. Custom-format dumps,
arbitrary ZIP layouts, encrypted entries and links are not supported. ZIP metadata,
CRC, source SHA-256 and referenced attachment sizes/checksums are checked. Use only
trusted backups: SQL and installed addon code are executable, not a safe format for
untrusted input. **This is development-only and performs no Odoo migration or
PostgreSQL downgrade.** It does not fetch or switch addon branches.

Defaults are conservative:

- Target is the selected instance's `dev`. An existing DB or filestore is rejected;
  `--replace` explicitly preserves the old pair under a generated recovery name.
- Missing PostgreSQL roles stop the operation before SQL import. Review the private
  role inventory, then opt into `--create-missing-roles` if appropriate. New roles
  are `NOLOGIN`, non-superuser, without database/role creation, inheritance,
  replication or RLS bypass. Existing roles are **not altered or privilege-audited**.
  Object grants/ownership from the dump still apply. Role membership grants,
  cluster administration and transaction-control statements are rejected by the
  bounded pg_dump reader, which is not a general-purpose SQL parser.
- Passwords are preserved. `--reset-admin-password` explicitly sets the password
  of `base.user_admin` to **`admin`**, keeping its login, permissions and MFA intact.
  It never assumes a numeric user ID or activates an inactive administrator.
- Modules are not updated unless requested. `--update-outdated` selects installed
  modules whose local manifest is newer, plus owners of detected missing stored
  columns, including same-version changes. `--update module_a,module_b` selects
  specific installed modules instead. The two modes are mutually exclusive;
  neither accepts `all`, installs arbitrary modules or permits a selected downgrade.
  Odoo may also update/install required dependencies according to its native rules.

Database-imported data modules are preserved without requiring local Python
manifests. They are not candidates for source-code updates; an explicit request
to update one is rejected. Registry validation respects Odoo's native loadable-module
domain instead of assuming every installed module must be loaded from disk.

For example, explicitly replace `dev`, resolve missing roles, update outdated
installed addons and set the weak local administrator password:

```sh
odx db restore /path/to/backup.zip --replace \
  --create-missing-roles --update-outdated --reset-admin-password
```

Close OpenCode clients and finish `run`, tests and dependency operations first.
Active managed operations or foreground OpenCode sessions reject the restore.
The workflow pauses only this instance's support containers and runs PostgreSQL
and the restore worker **without IP networking**, using a private Unix socket.
No Docker socket is mounted. The worker retains native environment/database leases
even if the host controller disappears; arbitrary external Docker operations remain
outside the locking protocol. Never run two PostgreSQL processes on the same volume.

Restoration happens in a generated staging DB and filestore. Before upgrades,
another DB/filestore checkpoint preserves the first imported copy. Native module
neutralization runs before registry hooks and again after upgrades/preparation;
all cron jobs and real mail servers must remain disabled across a fresh registry
load. Unknown integration behavior is not automatically neutralized: client addons
must supply their own native `data/neutralize.sql`, and a hook undoing neutralization
blocks publication. This is **not anonymization** or complete functional validation.

Only a validated pair is published as `dev`. Copy UUID/secret are regenerated and
the local URL is set after support containers restart. Odoo itself is not started:

```sh
odx run
odx status
```

### Continue a stopped restore

The command prints an operation ID and private recovery directory. Fix the cause
and continue with that ID rather than importing a large ZIP again:

```sh
odx db restore --resume <id> --create-missing-roles
odx db restore --resume <id> --update-outdated --reset-admin-password
# Or choose exact modules instead of --update-outdated:
odx db restore --resume <id> --update module_a,module_b
```

Explicit options are retained across retries; a new update mode replaces the
previous selection mode. Once publication begins, options cannot change. Completed
operations are idempotent. An import receipt committed in the SQL transaction
distinguishes a completed import from a rolled-back attempt. Publication records
native DB/file identities so a retry can reconcile partially completed renames
without merging directories, overwriting collisions, or discarding recovery copies.
Pending/partially updated databases or failed migrations can still require manual
review; `--resume` is not a generic migration repair tool. Instance configuration
must still match, while addon code may be corrected deliberately between retries.

Artifacts remain under `.odoocker/instances/<instance>/restores/<id>/`: `result.json`,
private logs, an engine snapshot, generated maintenance configuration and
`access.json`. The latter records the unchanged administrator login and current URL;
its password is `null` when the restored password was preserved. These files include
password hashes, copy secrets and potentially client data: **never commit or share
them**. Extracted data, update checkpoints, previous DB/filestore pairs, created roles
and the private socket volume are retained, not garbage-collected. Roles belong to
the instance's cluster and can remain even if the import fails; role creation
attempts/commit status are recorded. Budget disk space for the extracted SQL plus
DB/filestore checkpoints and PostgreSQL temporary/WAL space.

On failure, normal PostgreSQL and the idle Odoo support container are recovered
only after the isolated services stop. OpenCode may remain stopped until a
successful retry. If the host was killed, first inspect the operation: do not
interrupt a still-active worker. An explicit resume can recover stopped support
services when the worker has exited. Keep all generated resources for diagnosis;
do not remove volumes, copy `.odoocker`, or automatically retry with `-u all`.
On Linux, the current UID-1000 image must be able to read/write the private bind
directory; configurable UID/GID is not implemented.

## Multiple instances and agents

```sh
uv run odx --workspace ../workspaces/my-client --instance invoice-task up
uv run odx --workspace ../workspaces/my-client --instance invoice-task deps
```

Each instance has its own PostgreSQL, filestore, Python environment, network, and
OpenCode state. There is no global "active client" selector. Two workspaces with
the same directory name do not share an identity either.

**This does not create another copy of the code.** OpenCode retains its native
worktree management. Full host/container Git path integration still needs validation;
it is not presented as a finished feature. A worktree of the configuration does
not create worktrees for external addons repositories either.

## Tests an agent can run

Inside the OpenCode container, `odx` is installed and does not need Docker:

```sh
odx status
odx test --module my_module
odx test --module my_module --tags /my_module:MyClass
```

The same interface is used from the host:

```sh
uv run odx --workspace ../workspaces/my-client test \
  --module base --tags /base:TestACL.test_field_visibility_restriction
```

Each run creates its own database, filestore, data directory, and logs.
Host tests use a temporary container without published ports; inside an OpenCode
session, a dedicated port is coordinated. **`--no-http` does not guarantee that
Odoo will not open HTTP when tests are enabled**, so this resource is isolated too.

The result records the ID, exit code, and summary. Selecting zero tests is not
considered a success. Only that run's database and filestore are deleted, unless
`--keep` is used; logs/results remain in the runtime volume:

```sh
uv run odx --workspace ../workspaces/my-client logs --test ID
uv run odx --workspace ../workspaces/my-client logs --test ID --result
```

Locks prevent dependency installation while a managed Odoo operation uses the
environment, and prevent two managed operations from running against `dev`.
They do not coordinate external processes or prevent two agents from editing the
same file. A forced Docker shutdown/SIGKILL may leave test resources behind;
there is no automatic garbage collector yet. Do not use `down` during active operations.

### Initial profile limitations

- It does not yet include wkhtmltopdf with patched Qt, rtlcss, or a browser for
  tour tests. PDF/RTL/browser tests may fail because of these dependencies.
- The limited smoke test does not replace the full Odoo or client test suite.
- `deps` resolves discovered requirements together, using Odoo's requirements
  as constraints; this is not yet a reproducible locked installation.
- Change detection uses direct requirements; changes to `-r` includes must be
  applied by running `deps` explicitly.
- PostgreSQL is not exposed to the host. The included client matches the server's
  major version (16/18). Restore supports trusted native Odoo SQL ZIPs; an integrated
  backup command, custom-format imports and automatic recovery-copy cleanup remain
  pending.
- The image uses UID/GID 1000. On Linux, editable directories must allow that user
  to write; configurable UID/GID is still pending.
- Model credentials are configured on the new server with `/connect`;
  host credentials are not copied. An interactive conversation authenticated
  with a provider has not been tested yet.

## Diagnostics and shutdown

```sh
uv run odx --workspace ../workspaces/my-client status --json
uv run odx --workspace ../workspaces/my-client doctor
uv run odx --workspace ../workspaces/my-client logs opencode
uv run odx --workspace ../workspaces/my-client down
```

`down` removes **this instance's** containers and network, preserving all its
volumes. There is no global deletion command or automatic data cleanup.

## Engine development

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Unit tests do not need Docker or credentials. Live checks and their limitations
are recorded separately in `TODO.md`.
