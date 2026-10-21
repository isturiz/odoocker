# Odoocker

Shared development-only engine, not a copied client template. Python 3.12+, uv,
Docker Compose, and OpenCode V2. Only OpenCode is currently supported.

## Safety

- Run Docker commands with an explicit generated Compose file and project ID.
- Never remove volumes implicitly or mount the Docker socket in agent containers.
- No production management or automatic migration of existing workspaces.
- Keep secrets and machine-specific paths out of committed examples.

## Architecture

- `config.py`: validated declarative workspace and local configuration.
- `runtime.py`: resource identity and generated Docker/Odoo configuration.
- `cli.py`: host-facing commands; keep subprocess arguments as lists.
- `adapters/opencode.py`: OpenCode-specific connection behavior.
- `container.py`: in-container Odoo commands; no Docker access required.
- `reports.py`: native internal report URLs for the managed development database;
  preserve browser URLs and isolated test ports.
- `restore/`: host-scoped maintenance, offline workers and durable restore checkpoints;
  never publish an unverified DB/filestore pair. New restores dispose of their own
  update checkpoints only after successful host finalization, unless `--keep-checkpoints`;
  preserve unfinished/legacy recovery pairs and previous destinations from `--replace`.
- `odoo_compat.py`: inspect native API capabilities, not release-number branches.
  Validated releases are informational; retain backup/workspace version and downgrade guards.
- OpenCode owns its native worktrees. Do not implement a competing Git manager.
- Add adapters only for actual needs; no plugin framework or generic orchestrator.
- Keep README instructions and current limitations accurate; do not recreate a delivery
  log or speculative backlog. Distinguish automated tests from live validation in summaries.

## Versioning

- Increment the engine version once per completed change delivery, unless the user
  explicitly requests keeping the current version. Do not increment per file/edit.
- Use patch increments for fixes and maintenance, and minor increments for compatible
  functionality. Identify and document incompatible changes explicitly before choosing
  their release version; never silently change existing workspace/data contracts.
- `src/odoocker/__init__.py` is the only manually maintained engine version. Hatch reads
  it for package metadata; update the generated `uv.lock` with `uv lock` after a bump.
- Keep CLI output, package metadata and runtime image versions consistent. Do not change
  Odoo, PostgreSQL or OpenCode pins as part of an engine version bump.
- Do not create commits, release tags or publications without explicit authorization.

## Checks

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Use `uv run odx` to exercise the checkout without replacing global `odx`/`oc`.
Live Docker checks must only address workspaces created for this project.
