# SKILLS.md

## Purpose
This file documents repo-scoped Codex skills and how to add new ones for Odoo workflows.

## Where to store skills
- Keep repo skills in `.codex/skills/<skill-name>/SKILL.md` so they travel with the codebase.
- Each `SKILL.md` must include YAML front matter with `name` and `description`.
- Optional folders: `scripts/`, `references/`, `assets/`.
- Restart Codex after adding or updating skills.

## Suggested skills for this repo
- `sync-addons`: run `python clone_addons_repos.py`, report updated repos, and confirm `odoo.conf` updates.
- `run-odoo-devserver`: start Odoo from `/workspace/odoo` with `odoo.conf` and dev flags.
- `odoo-addon-review`: review an addon for Odoo 19 conventions (manifest, models, views, security, data).
- `odoo-addon-scaffold`: create a new addon skeleton in `addons/custom/<module>`.

## Skill template
```md
---
name: sync-addons
description: Sync addon repos using clone_addons_repos.py and refresh odoo.conf.
metadata:
  short-description: Sync addon repos
---

Run the addon sync script and summarize the changes in addons and odoo.conf.
```
