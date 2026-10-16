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
- OpenCode owns its native worktrees. Do not implement a competing Git manager.
- Add adapters only for actual needs; no plugin framework or generic orchestrator.
- Update `TODO.md` after each delivery; distinguish automated tests from live validation.

## Checks

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
```

Use `uv run odx` to exercise the prototype without replacing global `odx`/`oc`.
Live Docker checks must only address workspaces created for this project.
