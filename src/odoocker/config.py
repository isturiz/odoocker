"""Workspace definition, with private machine-specific overrides kept separate."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import OdxError

MANIFEST = "odoocker.toml"
LOCAL = "odoocker.local.toml"
NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
VERSION = re.compile(r"2\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?\Z")
VALIDATED_ODOO_VERSIONS = ("17.0", "19.0")
ODOO_VERSION = re.compile(r"[1-9][0-9]*\.0\Z")
ODOO_OPTIONS = {
    "limit_memory_hard",
    "limit_memory_soft",
    "limit_request",
    "limit_time_cpu",
    "limit_time_real",
    "limit_time_real_cron",
    "list_db",
    "dbfilter",
}


def validate_name(value, label="name"):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise OdxError(f"{label} must match {NAME.pattern}")
    return value


def validate_odoo_version(value):
    if not isinstance(value, str) or not ODOO_VERSION.fullmatch(value):
        raise OdxError("odoo.version must be a stable major version string, e.g. 18.0")
    return value


def find_workspace(start=None):
    path = Path(start or Path.cwd()).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / MANIFEST).is_file():
            return candidate
    raise OdxError(f"No {MANIFEST} found above {path}; use odx init or --workspace")


def _read(path):
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise OdxError(f"Cannot read {path}: {error}") from error


def _keys(data, allowed, label):
    if not isinstance(data, dict):
        raise OdxError(f"{label} must be a table")
    extra = data.keys() - allowed
    if extra:
        raise OdxError(f"Unknown {label} keys: {', '.join(sorted(extra))}")


def _path(root, value, label):
    if not isinstance(value, str) or not value.strip():
        raise OdxError(f"{label} must be a non-empty path")
    result = Path(value).expanduser()
    return (root / result).resolve()


def workspace_path(root, value, label):
    """Versioned paths stay inside this workspace, including through symlinks."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise OdxError(f"{label} must be a workspace-relative path")
    if ".." in Path(value).parts or value.startswith("~"):
        raise OdxError(f"{label} must stay inside the workspace")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or path == root:
        raise OdxError(f"{label} must stay inside the workspace")
    if path.is_relative_to(root / ".odoocker"):
        raise OdxError(f"{label} cannot use the generated .odoocker directory")
    return path


@dataclass(frozen=True)
class Repository:
    path: Path
    url: str
    branch: str


def _repositories(root, entries):
    if not isinstance(entries, list):
        raise OdxError("addons must be an array of repository tables")
    result = []
    for entry in entries:
        _keys(entry, {"path", "url", "branch"}, "addon repository")
        path = workspace_path(root, entry.get("path"), "addon.path")
        if not path.is_relative_to(root / "addons") or path == root / "addons":
            raise OdxError("addon.path must be underneath addons/")
        if ".git" in path.relative_to(root).parts:
            raise OdxError("addon.path cannot target Git metadata")
        url = entry.get("url")
        valid_url = False
        if isinstance(url, str) and not any(char.isspace() for char in url):
            try:
                parts = urlsplit(url)
            except ValueError as error:
                raise OdxError("addon.url is not a valid Git URL") from error
            valid_url = (
                parts.scheme in {"https", "ssh"}
                and bool(parts.hostname)
                and bool(parts.path.strip("/"))
                and not parts.password
                and not parts.query
                and not parts.fragment
                and (parts.scheme != "https" or not parts.username)
            ) or bool(re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./-]+", url))
        if not valid_url:
            raise OdxError("addon.url must be an HTTPS or SSH Git URL without embedded secrets")
        branch = entry.get("branch")
        if (
            not isinstance(branch, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]*", branch)
            or ".." in branch
            or any(
                not part or part.startswith(".") or part.endswith((".", ".lock"))
                for part in branch.split("/")
            )
        ):
            raise OdxError("addon.branch must be an explicit valid Git branch name")
        if any(
            path == repo.path or path.is_relative_to(repo.path) or repo.path.is_relative_to(path)
            for repo in result
        ):
            raise OdxError("Duplicate or overlapping addon repository paths")
        result.append(Repository(path, url, branch))
    return tuple(result)


@dataclass(frozen=True)
class Workspace:
    root: Path
    name: str
    odoo_version: str
    opencode_version: str
    source: Path
    addons: tuple[Path, ...] = ()
    repositories: tuple[Repository, ...] = ()
    requirement_files: tuple[Path, ...] = ()
    odoo_options: dict[str, int | bool | str] = field(default_factory=dict)
    postgres_version: str = "16"
    pgvector: bool = False

    @classmethod
    def load(cls, root):
        root = Path(root).resolve()
        data = _read(root / MANIFEST)
        _keys(data, {"schema", "workspace", "odoo", "opencode", "addons", "postgres"}, "manifest")
        if type(data.get("schema")) is not int or data["schema"] != 1:
            raise OdxError("Manifest schema must be 1")
        workspace = data.get("workspace", {})
        odoo = data.get("odoo", {})
        opencode = data.get("opencode", {})
        _keys(workspace, {"name"}, "workspace")
        _keys(odoo, {"version", "requirements", "options"}, "odoo")
        _keys(opencode, {"version"}, "opencode")
        postgres = data.get("postgres", {})
        _keys(postgres, {"version", "pgvector"}, "postgres")
        postgres_version = postgres.get("version", "16")
        if postgres_version not in ("16", "18"):
            raise OdxError('postgres.version must be "16" or "18"')
        pgvector = postgres.get("pgvector", False)
        if type(pgvector) is not bool:
            raise OdxError("postgres.pgvector must be a boolean")
        name = validate_name(workspace.get("name"), "workspace.name")
        version = validate_odoo_version(odoo.get("version"))
        code_version = opencode.get("version")
        if not isinstance(code_version, str) or not VERSION.fullmatch(code_version):
            raise OdxError("opencode.version must be an exact OpenCode V2 release, e.g. 2.0.30")
        local = _read(root / LOCAL) if (root / LOCAL).exists() else {}
        _keys(local, {"odoo"}, "local configuration")
        local_odoo = local.get("odoo", {})
        _keys(local_odoo, {"source", "addons"}, "local odoo")
        source = _path(root, local_odoo.get("source", ".odoocker/sources/odoo"), "odoo.source")
        addons = local_odoo.get("addons", [])
        if not isinstance(addons, list):
            raise OdxError("local odoo.addons must be an array of paths")
        repositories = _repositories(root, data.get("addons", []))
        paths = tuple(repo.path for repo in repositories) + tuple(
            _path(root, item, "odoo.addons") for item in addons
        )
        if len(set(paths)) != len(paths):
            raise OdxError("Duplicate addon paths")
        requirements = odoo.get("requirements", [])
        if not isinstance(requirements, list):
            raise OdxError("odoo.requirements must be an array of workspace-relative files")
        requirement_files = tuple(
            workspace_path(root, item, "odoo.requirements") for item in requirements
        )
        if len(set(requirement_files)) != len(requirement_files):
            raise OdxError("Duplicate workspace requirements files")
        options = odoo.get("options", {})
        _keys(options, ODOO_OPTIONS, "odoo.options")
        for key, value in options.items():
            if key == "list_db":
                if type(value) is not bool:
                    raise OdxError("odoo.options.list_db must be a boolean")
                continue
            if key == "dbfilter":
                if not isinstance(value, str) or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise OdxError("odoo.options.dbfilter must be a single-line string")
                try:
                    re.compile(value)
                except (re.error, OverflowError) as error:
                    raise OdxError(
                        "odoo.options.dbfilter must be a valid regular expression"
                    ) from error
                continue
            minimum = -1 if key == "limit_time_real_cron" else 0
            if type(value) is not int or value < minimum:
                raise OdxError(f"odoo.options.{key} must be an integer >= {minimum}")
        return cls(
            root,
            name,
            version,
            code_version,
            source,
            paths,
            repositories,
            requirement_files,
            options,
            postgres_version,
            pgvector,
        )

    def validate_core(self):
        if not (self.source / "odoo-bin").is_file():
            raise OdxError(f"Odoo source missing at {self.source}; run odx fetch")
        release = self.source / "odoo" / "release.py"
        if not release.is_file():
            raise OdxError(f"Missing Odoo release metadata: {release}")
        text = release.read_text()
        major = self.odoo_version.split(".")[0]
        if not re.search(rf"version_info\s*=\s*\(\s*{major}\s*,\s*0\s*,", text):
            raise OdxError(f"Source at {self.source} does not match Odoo {self.odoo_version}")

    def validate_sources(self):
        self.validate_core()
        for addon in self.addons:
            if not addon.is_dir():
                raise OdxError(f"Addon directory does not exist: {addon}; run odx fetch")
        for requirement in self.requirement_files:
            if not requirement.is_file():
                raise OdxError(f"Workspace requirements file does not exist: {requirement}")
