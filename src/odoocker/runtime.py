"""Generate instance-owned resources; never rely on ambient Compose context."""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__
from .config import Workspace, validate_name
from .errors import OdxError
from .locking import locked

PACKAGE = Path(__file__).parent


def write_private(path, content):
    """Replace complete files atomically; never publish half a config to another agent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_command(args, *, capture=False, env=None, check=True, cwd=None):
    try:
        return subprocess.run(
            [str(arg) for arg in args],
            check=check,
            text=True,
            capture_output=capture,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError as error:
        raise OdxError(f"Executable not found: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        # Do not echo arguments: they may include credentials.
        detail = (error.stderr or "").strip() if capture else ""
        raise OdxError(f"{args[0]} exited with status {error.returncode}: {detail}") from error


def _literals(value):
    """Compose interpolates even JSON strings; paths and secrets are literal values."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, dict):
        return {key: _literals(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_literals(item) for item in value]
    return value


def bind(source, target, *, readonly=False):
    return {
        "type": "bind",
        "source": str(source),
        "target": target,
        "read_only": readonly,
        "bind": {"create_host_path": False},
    }


def volume(name, target):
    return {"type": "volume", "source": name, "target": target}


@dataclass(frozen=True)
class Runtime:
    workspace: Workspace
    instance: str
    identity: str
    password: str

    @classmethod
    def load(cls, workspace, instance="main"):
        validate_name(instance, "instance")
        directory = workspace.root / ".odoocker"
        with locked(directory / "identity.lock"):
            state_file = directory / "identity.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                    identity = str(uuid.UUID(state["id"]))
                    owner = state["root"]
                except (ValueError, KeyError, TypeError) as error:
                    raise OdxError(f"Invalid workspace identity: {state_file}") from error
                if owner != str(workspace.root):
                    raise OdxError(
                        "Workspace state belongs to another path. Do not copy .odoocker; "
                        "create a fresh workspace instead. Existing volumes are unchanged."
                    )
            else:
                identity = str(uuid.uuid4())
                write_private(state_file, json.dumps({"id": identity, "root": str(workspace.root)}))
            credentials = directory / "instances" / instance / "secrets.json"
            if credentials.exists():
                try:
                    password = json.loads(credentials.read_text())["password"]
                except (ValueError, KeyError, TypeError) as error:
                    raise OdxError(f"Invalid credentials file: {credentials}") from error
                if not isinstance(password, str) or not password:
                    raise OdxError(f"Empty instance credentials: {credentials}")
            else:
                password = secrets.token_urlsafe(32)
                write_private(credentials, json.dumps({"password": password}))
        return cls(workspace, instance, identity, password)

    @property
    def project(self):
        return f"odx-{self.identity.replace('-', '')}-{self.instance}"

    @property
    def directory(self):
        return self.workspace.root / ".odoocker" / "instances" / self.instance

    @property
    def compose_file(self):
        return self.directory / "compose.json"

    @property
    def operation_lock(self):
        return self.directory / "operation.lock"

    def image(self):
        digest = hashlib.sha256()
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".Dockerfile"}:
                digest.update(str(path.relative_to(PACKAGE)).encode())
                digest.update(path.read_bytes())
        prefix = f"odoocker-dev:{__version__}-{digest.hexdigest()[:12]}"
        return f"{prefix}-pg{self.workspace.postgres_version}-oc{self.workspace.opencode_version}"

    @property
    def database_volume(self):
        # Retain the historical PG16 name; incompatible majors never share storage.
        return "db-data" if self.workspace.postgres_version == "16" else "db-data-pg18"

    def compose_document(self):
        major = self.workspace.postgres_version
        database_image = (
            f"pgvector/pgvector:0.8.7-pg{major}-bookworm"
            if self.workspace.pgvector
            else {"16": "postgres:16.13-bookworm", "18": "postgres:18-bookworm"}[major]
        )
        mounts = [
            bind(self.workspace.root, "/workspace"),
            bind(self.workspace.source, "/opt/odoo", readonly=True),
            bind(self.directory / "odoo.conf", "/etc/odoocker/odoo.conf", readonly=True),
            bind(self.directory / "context.json", "/etc/odoocker/context.json", readonly=True),
            volume("venv", "/opt/venv"),
            volume("odoo-data", "/home/odoo/.local/share/Odoo"),
            volume("runtime", "/var/lib/odoocker"),
        ]
        mounts.extend(
            bind(path, f"/opt/addons/{index}") for index, path in enumerate(self.workspace.addons)
        )
        common = {
            "image": self.image(),
            "build": {
                "context": str(self.directory / "build"),
                "dockerfile": "runtime.Dockerfile",
                "args": {
                    "OPENCODE_VERSION": self.workspace.opencode_version,
                    "POSTGRES_VERSION": major,
                },
            },
            "working_dir": "/workspace",
            "init": True,
            "environment": {"ODOOCKER_CONTAINER": "1"},
            "labels": {
                "io.odoocker.configuration": hashlib.sha256(
                    json.dumps(asdict(self.workspace), default=str, sort_keys=True).encode(),
                ).hexdigest()
            },
            "depends_on": {
                "init": {"condition": "service_completed_successfully"},
                "db": {"condition": "service_healthy"},
            },
            "volumes": mounts,
        }
        odoo = {
            **common,
            "command": ["sleep", "infinity"],
            "ports": [{"target": 8069, "host_ip": "127.0.0.1"}],
        }
        opencode = {
            **common,
            "profiles": ["code"],
            "command": ["odx", "_serve"],
            "environment": {"ODOOCKER_CONTAINER": "1", "OPENCODE_PASSWORD": self.password},
            "ports": [{"target": 4096, "host_ip": "127.0.0.1"}],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python3",
                    "-c",
                    ("import socket; socket.create_connection(('127.0.0.1', 4096), 2).close()"),
                ],
                "interval": "2s",
                "timeout": "3s",
                "retries": 30,
            },
            "volumes": [
                *mounts,
                volume("opencode-config", "/home/odoo/.config/opencode"),
                volume("opencode-data", "/home/odoo/.local/share/opencode"),
                volume("opencode-state", "/home/odoo/.local/state/opencode"),
            ],
        }
        return {
            "name": self.project,
            "services": {
                "init": {
                    "image": "busybox:1.37.0",
                    "user": "0:0",
                    "command": ["chown", "1000:1000", "/venv", "/runtime", "/data"],
                    "volumes": [
                        volume("venv", "/venv"),
                        volume("runtime", "/runtime"),
                        volume("odoo-data", "/data"),
                    ],
                },
                "db": {
                    "image": database_image,
                    "shm_size": "256m",
                    "environment": {
                        "POSTGRES_USER": "odoo",
                        "POSTGRES_PASSWORD": self.password,
                        "POSTGRES_DB": "postgres",
                    },
                    "healthcheck": {
                        "test": ["CMD-SHELL", "pg_isready -U odoo -d postgres"],
                        "interval": "2s",
                        "timeout": "3s",
                        "retries": 30,
                    },
                    "volumes": [
                        volume(
                            self.database_volume,
                            "/var/lib/postgresql" if major == "18" else "/var/lib/postgresql/data",
                        )
                    ],
                },
                "odoo": odoo,
                "opencode": opencode,
            },
            "volumes": {
                name: {}
                for name in (
                    "venv",
                    "runtime",
                    self.database_volume,
                    "odoo-data",
                    "opencode-config",
                    "opencode-data",
                    "opencode-state",
                )
            },
        }

    def generate(self, *, allow_database_switch=False):
        self.workspace.validate_sources()
        with locked(self.operation_lock):
            previous = self.directory / "context.json"
            if previous.exists():
                version = json.loads(previous.read_text()).get("version")
                if version != self.workspace.odoo_version:
                    raise OdxError(
                        "Do not reuse an instance's database and environment across Odoo versions. "
                        "Create another workspace or select a new --instance."
                    )
                old_major = json.loads(previous.read_text()).get("postgres_version", "16")
                if old_major != self.workspace.postgres_version and not allow_database_switch:
                    raise OdxError(
                        f"PostgreSQL changes from {old_major} "
                        f"to {self.workspace.postgres_version}. "
                        "Stop this instance with odx down, then configure --allow-database-switch "
                        "to select the other major's independent volume. No data is migrated or "
                        "deleted; switching back reuses that major's existing volume."
                    )
            build = self.directory / "build"
            build.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PACKAGE / "runtime.Dockerfile", build / "runtime.Dockerfile")
            shutil.copytree(
                PACKAGE,
                build / "odoocker",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            addons = ["/opt/odoo/odoo/addons", "/opt/odoo/addons"]
            addons.extend(f"/opt/addons/{index}" for index in range(len(self.workspace.addons)))
            config = (
                "[options]\n"
                "db_host = db\ndb_port = 5432\ndb_user = odoo\n"
                f"db_password = {self.password}\n"
                "db_name = dev\ndbfilter = ^dev$\n"
                "data_dir = /home/odoo/.local/share/Odoo\n"
                f"addons_path = {','.join(addons)}\n"
                "http_interface = 0.0.0.0\nhttp_port = 8069\n"
                "workers = 0\nmax_cron_threads = 0\nlist_db = False\n"
                "unaccent = True\n"
            )
            config += "".join(
                f"{key} = {value}\n" for key, value in sorted(self.workspace.odoo_options.items())
            )
            # Config must be readable by UID 1000 inside Docker; directory stays private.
            for name, content in (
                ("odoo.conf", config),
                (
                    "context.json",
                    json.dumps(
                        {
                            "project": self.project,
                            "instance": self.instance,
                            "workspace": self.workspace.name,
                            "version": self.workspace.odoo_version,
                            "postgres_version": self.workspace.postgres_version,
                            "pgvector": self.workspace.pgvector,
                            "addons": addons[2:],
                            "requirements": [
                                f"/workspace/{path.relative_to(self.workspace.root).as_posix()}"
                                for path in self.workspace.requirement_files
                            ],
                        }
                    ),
                ),
            ):
                write_private(self.directory / name, content)
                (self.directory / name).chmod(0o644)
            self.directory.chmod(0o700)
            write_private(self.directory / "compose.env", "")
            write_private(
                self.compose_file, json.dumps(_literals(self.compose_document()), indent=2)
            )

    def compose(self, *args, capture=False, check=True):
        if not self.compose_file.exists():
            raise OdxError("Runtime is not generated; run odx configure first")
        if args and args[0] not in {"ps", "port", "logs", "down", "--profile"}:
            generated = json.loads(self.compose_file.read_text())
            if generated != _literals(self.compose_document()):
                raise OdxError(
                    "Workspace or engine configuration changed; run odx configure, then odx up "
                    "before using this instance. No running services were changed."
                )
        env = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
        return run_command(
            [
                "docker",
                "compose",
                "--project-directory",
                str(self.directory),
                "--env-file",
                str(self.directory / "compose.env"),
                "--project-name",
                self.project,
                "--file",
                str(self.compose_file),
                *args,
            ],
            capture=capture,
            check=check,
            env=env,
        )

    def url(self, service, port):
        result = self.compose("port", service, str(port), capture=True)
        address = result.stdout.strip()
        if not address.startswith("127.0.0.1:") or not address.removeprefix("127.0.0.1:").isdigit():
            raise OdxError(f"No localhost port for {service}; run odx up first")
        return f"http://{address}"
