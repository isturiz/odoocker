# AGENTS.md

## Project overview
- Repo for an Odoo 19 workspace with addons in `addons/` and optional enterprise code mounted at `/workspace/enterprise`.
- Odoo source is expected at `/workspace/odoo` (mounted via the devcontainer).
- Configuration lives in `odoo.conf`, including `addons_path` and DB settings.

## Dev environment
- Primary workflow uses the devcontainer in `.devcontainer/devcontainer.json` and `.devcontainer/compose.yaml` (services: `odoo`, `pgdb`).
- Python 3.12 is required (`.python-version`), with a virtualenv at `/workspace/.venv`.
- Dependency install (per `README.md`):
  - `uv sync`
  - `uv pip install -r odoo/requirements.txt`

## Addons management
- Declare addon repos in `addons_repos.toml` under `third-party`, `oca`, or `custom`.
- Run `python clone_addons_repos.py` to clone/update repos into `/workspace/addons/<section>` and refresh `addons_path` in `odoo.conf`.
- Optional env vars: `ADDONS_BASE_DIR` (base path), `ADDONS_CONFIG` (config path).

## Running Odoo
- Use `odoo.conf` for ports and DB (host `pgdb`, user/password `odoo`).
- From `/workspace/odoo`, run `./odoo-bin -c /workspace/odoo.conf --dev=all` (or `odoo --dev=all` if available).

## Testing
- No dedicated test runner is configured in this repo.
- For addon tests, use Odoo built-in testing with `./odoo-bin -c /workspace/odoo.conf --test-enable -i <module>` and focus on changed modules.

## Conventions
- Follow standard Odoo addon structure: `__manifest__.py`, `__init__.py`, `models/`, `views/`, `security/`.
- Prefer changes in `addons/custom` and avoid editing OCA/third-party code unless required by the task.

## Odoo coding guidelines
- Follow the official Odoo coding guidelines for new code; see `ODOO_CODING_GUIDELINES.rst`.
- When editing existing files in stable branches, keep the original style and minimize diffs.
- In development branches, apply guidelines only to changed code unless doing large refactors (split move + feature).
- Respect Odoo module structure and file naming rules for models, views, security, data, reports, and wizards.
- Follow XML ID naming conventions for views/actions/menus/security and keep translation strings literal.
- Avoid manual `cr.commit()`/`cr.rollback()` unless you created your own cursor; use savepoints for handled exceptions.
