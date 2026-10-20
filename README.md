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

### Stable browser URLs (optional)

The engine can manage **one native Caddy process shared by all workspaces**.
Install Caddy separately (on macOS, `brew install caddy`; on Linux, use Caddy's
official installation instructions). No system service, root privileges, Docker
socket, certificates, external DNS, or `/etc/hosts` changes are needed.

```sh
odx proxy start                 # Once per host session, from any directory
cd /path/to/client
odx proxy register              # Register an already-prepared running instance
odx status                     # Includes odoo_proxy_url and the direct odoo_url
```

For a workspace named `client`, instance `main`, the stable URL is:

```text
http://client-main.localhost:8080
```

After the first `proxy start`, `up`, `setup`, and successful OpenCode updates
automatically refresh the selected instance's running Odoo/OpenCode routes. `down`
removes only that instance's routes, not the shared proxy. Odoo still needs `odx run`
to serve HTTP; start OpenCode with `up --code` or `setup --code`.
Existing workspaces are not discovered or changed automatically: use `register`
or their next normal startup. Instances keep separate hostnames and browser cookies.

```sh
odx --instance testing proxy register
odx proxy status
odx proxy stop                 # Stop only this proxy; preserve routes and opt-in
odx proxy start                # Restart it, including after a host reboot
odx proxy remove client-testing.localhost  # Remove one exact stale route
```

- Caddy listens only on `127.0.0.1`, HTTP, default port **8080**. Choose a different
  unprivileged port on the first start with `--port 8088`; it stays fixed afterward.
- State and private logs live under `${XDG_DATA_HOME:-~/.local/share}/odoocker/proxy/`.
  Its admin API uses a Unix socket inside that private directory; an unrelated
  Caddy service/profile is never stopped or reconfigured.
- Names use `<workspace>-<instance>.localhost`, normalizing underscores to hyphens.
  The combined DNS label must fit in 63 characters; collisions are rejected rather
  than reassigned. Give simultaneous workspaces distinct names.
- Route updates are locked across workspaces and Caddy reloads atomically. Proxy
  failures produce warnings without hiding a successful Docker operation. Retry
  with `proxy register`; direct access remains available when Caddy is unavailable.
- Use `odx` for container recreation: external Docker changes do not refresh routes.
  Remove routes for abandoned workspaces explicitly; there is no Docker discovery.
- OpenCode uses `http://opencode-<workspace>-<instance>.localhost:8080`.
  `odx code opencode` and `--info` refresh and select that stable route when the
  proxy is active, retaining the instance password and isolated client state.
  With no proxy configured they use the direct port; a stopped proxy produces a
  warning and direct-port fallback. Start the proxy **before opening the client**
  to benefit from the stable URL. Already-open direct connections must be relaunched
  once after adopting it. Refreshes require `odx` startup commands or explicit
  `proxy register` if containers are recreated externally.
- The proxy preserves the HTTP Host and authentication headers, immediately flushes
  event streams, and supports WebSocket upgrades (with a five-minute close delay
  during configuration reloads). The stable URL survives server port changes, but
  downtime still interrupts connections and running operations. Recovery of an
  already-open TUI depends on OpenCode's own reconnect behavior, not the proxy.
  PostgreSQL is never proxied. The proxy does not enable Odoo's
  `proxy_mode`: HTTP scheme/host remain unchanged, with no TLS termination.

Browsers normally resolve `*.localhost` to loopback. For command-line diagnostics
on systems without that resolver behavior, use e.g.
`curl --resolve client-main.localhost:8080:127.0.0.1 http://client-main.localhost:8080`.

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
`odoo.options` accepts memory, time, and request limits, plus the database-manager
options described below. It does not allow overriding the PostgreSQL connection,
ports, credentials, managed paths, or the primary `dev` database.

Private `odoocker.local.toml` file:

```toml
[odoo]
source = "/local/path/to/odoo"
addons = ["./addons/my-client", "/path/to/oca-repository"]
```

Local Odoo paths are optional and are added to the declared repositories. There is no
need to repeat `[[addons]]` here; duplicate paths are rejected.

Each addons entry is a directory whose children are modules. They are mounted
read/write for development. Relative paths are resolved from the workspace, not
from the terminal. SSH keys, the Docker socket, and the global OpenCode profile
are not mounted. Host skills can be shared separately, as described below.
Project files such as `.opencode/` are part of the mounted
workspace and can be read by the agent.

The CLI rejects unknown keys to catch configuration errors early.
After changing the manifest, paths, or engine version, run `configure` and `up`
to regenerate/recreate. Do not regenerate while another session is using the instance.

### Share host OpenCode skills (optional)

Opt in per workspace and machine in **`odoocker.local.toml`**, which must remain
excluded from Git. Share the complete global skills directory:

```toml
[opencode]
skill_sources = ["~/.config/opencode/skills"]
```

Or select individual directory skills or other skill collections:

```toml
[opencode]
skill_sources = [
  "~/shared-skills/odoo-review",
  "~/shared-skills/release",
]
```

These are illustrative paths, not installed defaults. Use the actual skills path
if your host uses a custom `XDG_CONFIG_HOME`; no host OpenCode command or profile
import is performed. Omitted `skill_sources` or `[]` keeps the original behavior.
This setting is rejected in the version-controlled `odoocker.toml`.

Each source must be an existing directory containing skills: root-level `*.md`
files, or nested directories with `SKILL.md`. A source containing its own
`SKILL.md` is treated as an individual directory skill. Its configured directory
name supplies the ID, including when that directory is a symlink; the frontmatter
`name` is only a display label. Nested directory skills retain their own IDs.
For individual skills, the engine registers a virtual parent directory, avoiding
OpenCode V2's root-level `SKILL.md` ID of `SKILL`.

The engine mounts each complete source **read-only**, only in the OpenCode
service, under `/mnt/odoocker/skills/<index>`. Scripts, references, templates,
executable permissions and other supporting files remain alongside the skill.
There is no copying, synchronization script, host Docker socket, or profile mount.
Only an engine-generated `skills` array is mounted as the container's system
configuration at `/etc/opencode/opencode.json`. The independent OpenCode
configuration/data/state volumes remain unchanged; host credentials, general
configuration, plugins and state outside the selected directories are not exposed.

**Duplicate IDs:** OpenCode's native explicit sources override automatically
discovered global/container and project skills. The engine's system-level entries
also follow lower-priority explicit config entries. Within `skill_sources`, the
**last source wins**. Earlier definitions are not overwritten or deleted; removing
the shared source reveals them again. Ambiguous duplicate IDs *inside a single
host source* are rejected: select separate sources in the desired order or give
the skills distinct IDs. Avoid collisions unless the override is intentional.

Path rules and limitations:

- `~` expands on the host; relative paths resolve from the workspace, not the shell.
- Symlinks in source paths are resolved before mounting. For example,
  `~/.config/opencode -> ~/config/opencode` works without mounting that profile.
- Duplicate/overlapping canonical sources, missing/empty sources, unreadable
  trees, special files and profile roots are rejected. Source roots containing
  `opencode.json(c)`, `cli.json` or `auth.json` are also rejected to catch profile
  selection mistakes. All files *inside* an accepted source are shared: select
  trusted skills directories and do not put secrets in them.
- Relative symlinks inside a source are supported only if they remain inside that
  source. Broken links, cycles, absolute links (even to host-internal files), and
  links to unselected siblings are rejected. Select a common collection root or
  make the supporting files self-contained instead of exposing more host paths.
- Read-only mounts prevent writes through the shared paths, not execution of
  untrusted scripts or writes through another independently writable workspace/addon
  mount. Skills are executable instructions, not a sandbox. The container's UID
  1000 must be able to read/traverse the sources; real Linux permissions still apply.
- File edits within an existing mount are visible without a copy step; OpenCode's
  own discovery/cache controls when they are reloaded. After changing sources or
  symlink targets, deliberately reconfigure/recreate the instance. No hot-reload
  guarantee is made.

Apply only when the instance is idle, using the checkout if testing the engine:

```sh
uv run --project /path/to/odoocker odx down
uv run --project /path/to/odoocker odx configure
uv run --project /path/to/odoocker odx up --code
```

To disable, remove the setting or set it to `[]`, then repeat that explicit
workflow. No container/project skills or volume contents are deleted, including
files added independently in the container. An old generated `opencode-skills.json`
may remain under `.odoocker/instances/<instance>/`; it is no longer mounted and
contains only container search paths, not copied skill content.

See the official V2 [skills](https://opencode.ai/v2/docs/skills) and
[configuration](https://opencode.ai/v2/docs/config) documentation for native
discovery and precedence. Automated filesystem/Compose tests do not establish
live discovery or read-only enforcement by Docker/OpenCode; see `TODO.md`.

### Odoo web database manager

The web manager is **disabled by default** (`list_db = false`), and HTTP database
selection is restricted to `dev` (`dbfilter = "^dev$"`). Enable it explicitly for
development in the existing `[odoo.options]` table in `odoocker.toml`:

```toml
[odoo.options]
list_db = true
# Optional: allow dev plus deliberately named disposable development copies.
dbfilter = "^(dev|sandbox_[a-z0-9_]+)$"
```

`list_db` must be a boolean; `dbfilter` must be a single-line regular expression.
Odoo's `%h` and `%d` hostname substitutions are supported. Omitting the filter keeps
`^dev$`; an empty filter falls back to Odoo's explicit primary database selection.
Changing the filter alone does not enable the manager. Ports remain published only
on `127.0.0.1`, and PostgreSQL is not published.

After explicitly upgrading the engine, apply with no active operations or sessions:

```sh
odx down
odx configure
odx up --code # Omit --code if not using OpenCode.
odx run
```

Use the Odoo URL reported by `odx status`, followed by `/web/database/manager`.
On first enablement, the engine creates an independent random master password per
instance in `.odoocker/instances/<instance>/secrets.json` (`admin_password`), and
writes it as `admin_passwd` in the generated `odoo.conf`. Retrieve it privately from
that file; do not commit it, put it in the manifest, or post it in logs. The password
survives regeneration and disabling/re-enabling the manager. Existing PostgreSQL
and OpenCode credentials are not rotated. The generated configuration is read-only
inside Odoo: manage this secret on the host, not through the web password-change form.

The primary database remains `dev`: `odx run --init/--update`, restores and `passwd`
retain that target. An expanded HTTP filter permits selecting other matching
databases in the browser; it does not retarget those CLI operations.

**Web operations bypass odoocker's maintenance leases, checkpoint protections,
restore verification and neutralization workflow.** Use the CLI for validated
restores and recovery-copy management. Avoid broad filters such as `.*`: they can
expose test/recovery databases to HTTP selection. Do not create, delete, duplicate
or restore databases through the web while managed tests or maintenance are active.
Development copies can still contain sensitive data and live integrations.

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

Restore behavior and options:

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
- After a successfully finalized restore, its pre-update DB/filestore checkpoint
  is deleted automatically. Use `--keep-checkpoints` to retain that recovery pair
  explicitly. This does not affect the original ZIP, `dev`, logs, or a previous
  destination preserved by `--replace`. Failed/incomplete restores retain their
  checkpoints; older operations without this retention policy also keep them on resume.
- After a successfully finalized restore, the extracted `dump.sql` is deleted
  automatically. Use `--keep-sql` to retain it explicitly. Failed or incomplete
  restores always keep the SQL for diagnosis and resumption. The original ZIP is
  never deleted or modified.

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
another DB/filestore checkpoint preserves the first imported copy until successful
host finalization (or longer with `--keep-checkpoints`). Native module
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

### Database inventory and explicit deletion

```sh
odx db list
odx db list --json
odx db drop exact_database_name --dry-run
odx db drop exact_database_name
# Noninteractive, explicitly destructive:
odx db drop exact_database_name --yes
```

These commands work on the host and inside the Odoo/OpenCode containers, without
giving agents Docker access. Inventory shows PostgreSQL sizes (not filestore/WAL
sizes), connection counts, protected databases and development/test/recovery names.
The scope is **only the selected instance's PostgreSQL cluster**, not every Docker
project. `--instance` and `--workspace` are host options; container context is fixed.
After installing an engine release containing these commands, explicitly run
`odx setup --code` in an idle workspace to prepare the matching in-container CLI;
upgrading the host CLI alone does not update running containers.

`drop` accepts one exact name, never patterns or `--all`, and deletes **both the
database and its filestore**. It requires typing `delete NAME`, or explicit `--yes`
without a terminal. `--dry-run` makes no deletions. Templates, `postgres`, databases
owned by other roles and databases with active connections are rejected. The native
PostgreSQL drop does not use `FORCE` or terminate clients. Database and filestore
identities are checked again after confirmation. Stop managed Odoo runs/tests and
shells before deleting: drop holds an exclusive environment maintenance lease.

Deleting a named recovery copy deliberately loses that checkpoint. Copies belonging
to unfinished restores are protected; fix/resume those operations first. Retained
tests use their run-specific filestore, not `dev`'s. Missing databases do not trigger
orphan-filestore deletion. No volumes, original ZIPs, logs or SQL inputs are removed.

There is no transaction across PostgreSQL and the filesystem. Each attempt records
private state under `/var/lib/odoocker/db-drops/<id>/` in the runtime volume. A failed
filestore cleanup reports partial deletion and retains the remaining files, possibly
in the recorded quarantine path. Inspect the record rather than repeating the drop
or broadly removing directories. External, unmanaged SQL/Docker operations are not
coordinated by these locks; do not recreate or change the selected resources during
maintenance.

### Change a development password

```sh
odx passwd
odx passwd --login exact-login
# Automation: pipe one password line from a secret source, never a CLI argument.
secret-source | odx passwd --password-stdin
```

The target database is always this instance's `dev`. By default the account is
identified by `base.user_admin`, not a hard-coded login or numeric ID. `--login`
selects an exact account, including inactive accounts without activating them.
Interactive input is hidden and confirmed; there is no default password and no
`--password` argument. Noninteractive use requires `--password-stdin`, accepting
one nonempty line (maximum 4096 characters). Do not put secrets in shell history.

The command uses Odoo's native hashing API in a transaction and verifies the stored
hash before committing. Login, activation, privileges and MFA are not reset.
Dependencies must be prepared; stop `odx run` first. Pending/partial module updates,
version mismatches and missing installed Python addons block the operation. No HTTP
or cron server is started, although loading the native registry runs module hooks
as a normal Odoo startup does. This is not a production account-management tool.

### Open a service terminal

```sh
odx sh odoo
odx sh pg
odx sh opencode
odx sh pg --psql
```

Choose the service explicitly. `odoo` and `opencode` open Bash; `pg` opens the
PostgreSQL container's system shell as the OS user `postgres`, not root.
`--psql` is only valid with `pg` and opens a
clean psql session connected to `dev`. These are **host-only** commands and use
already running containers; they do not start/recreate services or open an Odoo ORM
console. EOF or `exit` closes the terminal. TTY allocation follows the caller so
stdin can also be piped. Shells retain the host operation lock, and Odoo/OpenCode
shells also retain a shared Linux environment lease to block dependency replacement
and maintenance. Arbitrary commands inside a shell are still the user's responsibility.

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
them**. Disposable SQL and the pre-update checkpoint are removed after success,
unless `--keep-sql` and `--keep-checkpoints`, respectively, were selected. These
independent retention options persist across retries. Previous DB/filestore pairs
from `--replace`, small extraction metadata, logs, created roles and the private
socket volume are retained. If cleanup fails, the command warns without treating
the restored `dev` as failed; `--resume <id>` retries pending cleanup only on a
finalized operation, without reimporting or stopping services. Checkpoint disposal
holds exclusive native leases, verifies recorded database OIDs/filestore inodes,
and journals partial deletion in `checkpoint-cleanup.json` for safe retry. It never
terminates database clients or deletes another operation's recovery pairs. Older
operations retain their checkpoints; their disposable SQL can still be cleaned on
explicit resume. Finish managed Odoo operations before retrying checkpoint cleanup;
an occupied environment/database lease leaves it pending without interrupting work.
Roles belong to the instance's cluster and can remain even if the import fails;
role creation attempts/commit status are recorded. Budget disk space for the extracted SQL plus
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

### PDF reports (included by default)

The shared Odoo/OpenCode image includes **wkhtmltopdf 0.12.6.1 (with patched Qt)**
from upstream's `0.12.6.1-3` Bookworm packages, plus its runtime/font dependencies.
There is no feature flag and no host installation is needed. Both `amd64` and
`arm64` packages are pinned with SHA-256 checks; unsupported architectures fail
the build rather than silently omitting report support. The build also checks
the executable's version and patched-Qt marker.

Odoo invokes this external executable to convert report HTML to PDF. The patched
Qt build supports the headers and footers used by Odoo; the ordinary Debian
`wkhtmltopdf` package is not a substitute. This follows the
[Odoo dependency guidance](https://www.odoo.com/documentation/19.0/administration/on_premise/source.html#dependencies).
The engine does not patch Odoo or enable unrestricted local-file access.

Existing containers do not gain system packages from a host CLI upgrade or
`odx deps`. After explicitly installing the updated engine, close clients and
finish managed operations, then rebuild/recreate the selected instance:

```sh
odx down
odx configure
odx up --code # Omit --code if not using OpenCode; do not use --no-build here.
odx run
```

This preserves volumes, databases, filestore and OpenCode state, without changing
the configured Odoo/PostgreSQL/OpenCode versions. For checkout testing, prefix each
command with `uv run --project /path/to/odoocker`. Repeat explicitly for other
instances as needed.

**Report assets are configured automatically for the managed `dev` database.**
Before serving HTTP, `odx run` sets Odoo's native database parameter `report.url`
to `http://127.0.0.1:<http_port>` (normally `8069`), so wkhtmltopdf fetches CSS and
images from the Odoo process in its own container. It does not use the host's
dynamic published port or Caddy's `*.localhost` route for these requests.
`web.base.url` and `web.base.url.freeze` remain unchanged by `run`: the browser's
address and report asset address are independent.

This is automatic, with no feature flag or manual parameter setup. An existing
`report.url` in `dev`, including a copied production URL, is replaced with the
internal endpoint; an already correct value is left untouched. Configuration is
applied under the managed environment/database leases, before loading the normal
Odoo server, without an extra registry load, addon, Odoo patch or DNS override.
No database is created by the parameter preparation itself. For an explicit first
`run --init ...` on an absent/uninitialized `dev`, Odoo initializes natively with
`--stop-after-init --no-http`; the engine then sets the parameter. Unless you also
requested `--stop-after-init`, it starts the server without repeating the consumed
install/update flags. Successful offline initializations/upgrades also recheck the
parameter before returning. Failures do not continue to HTTP startup.

Restore finalization sets the same internal `report.url` together with its usual
local browser URL, before marking the host operation finalized or disposing of
checkpoints. `setup` still does not create/initialize/change databases; `odx test`
keeps its own isolated database and dynamically selected HTTP port, without running
the `dev` parameter preparation. Other development copies and Odoo launches outside
`odx run` are not automatically configured. Custom report/layout failures still
require their own diagnosis. Adopt the updated **in-container** engine explicitly;
upgrading only the host CLI does not change an already running container's `odx run`.

### Initial profile limitations

- It does not yet include rtlcss or a browser for tour tests. RTL/browser tests
  may fail because of these dependencies; PDF support is included as described above.
- The limited smoke test does not replace the full Odoo or client test suite.
- `deps` resolves discovered requirements together, using Odoo's requirements
  as constraints; this is not yet a reproducible locked installation.
- Change detection uses direct requirements; changes to `-r` includes must be
  applied by running `deps` explicitly.
- PostgreSQL is not exposed to the host. The included client matches the server's
  major version (16/18). Restore supports trusted native Odoo SQL ZIPs; an integrated
  backup command, custom-format imports and cleanup of older/previous-destination
  copies remain pending.
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
volumes. There is no global deletion command or volume cleanup; successful restores
only dispose of their own temporary SQL and pre-update checkpoint by default.

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
