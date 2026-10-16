"""Fetch missing declared checkouts; never pull, reset or switch existing user work."""

import os
import shutil
import uuid

from .errors import OdxError
from .runtime import run_command


def _url_identity(url):
    # GitHub commonly spells the same SSH origin with or without the .git suffix.
    return url.rstrip("/").removesuffix(".git")


def validate_checkout(repository):
    path = repository.path
    if not path.is_dir() or not (path / ".git").exists():
        raise OdxError(f"Refusing to replace existing non-Git path: {path}")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    root = run_command(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture=True,
        env=env,
    ).stdout.strip()
    if str(path.resolve()) != root:
        raise OdxError(f"Expected an independent checkout at {path}")
    origin = run_command(
        ["git", "-C", path, "remote", "get-url", "origin"],
        capture=True,
        env=env,
    ).stdout.strip()
    if _url_identity(origin) != _url_identity(repository.url):
        raise OdxError(f"Existing checkout has another origin: {path}; left unchanged")
    branch = (
        run_command(
            ["git", "-C", path, "branch", "--show-current"],
            capture=True,
            env=env,
        ).stdout.strip()
        or "detached HEAD"
    )
    print(f"Keep {path}: {branch} (declared: {repository.branch}); no pull or checkout")


def fetch_repositories(workspace, *, dry_run=False):
    for repository in workspace.repositories:
        destination = repository.path
        # Recheck in case an existing path/parent was replaced by a symlink after loading.
        if destination.resolve() != destination or not destination.is_relative_to(workspace.root):
            raise OdxError(f"Addon destination escaped the workspace: {destination}")
        if destination.exists():
            validate_checkout(repository)
            continue
        print(f"Clone {repository.url} @ {repository.branch} -> {destination}")
        if dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.partial")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            run_command(
                [
                    "git",
                    "clone",
                    "--single-branch",
                    "--branch",
                    repository.branch,
                    "--",
                    repository.url,
                    temporary,
                ],
                env=env,
            )
            # Never replace even an empty directory that another process created.
            if destination.exists():
                raise OdxError(
                    f"Destination appeared while cloning; refusing to replace {destination}"
                )
            temporary.rename(destination)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
