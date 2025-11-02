import json
import os
import shutil
import subprocess
from pathlib import Path

import click

from .utils import find_project_root

VENDOR_DIR = Path("vendor")


def get_env(name: str, required: bool = True) -> str:
    val = os.getenv(name)
    if required and not val:
        raise click.UsageError(f"{name} not set in .env")
    return val or ""


def ensure_project_root() -> Path:
    """Asegura que el comando se ejecuta en la raíz del proyecto."""
    _ = find_project_root()
    return Path.cwd()


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Ejecuta un comando en subprocess y devuelve el resultado."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def get_container_image_id(container: str) -> str:
    res = run(["docker", "inspect", container])
    if res.returncode != 0:
        raise click.ClickException(f"Cannot inspect container {container}: {res.stderr.strip()}")
    data = json.loads(res.stdout)[0]
    return data["Image"]


def has_vendor_mount(container: str) -> bool:
    """Detecta si el contenedor tiene un volumen montado en /usr/lib/python3/dist-packages/odoo"""
    res = run(["docker", "inspect", container])
    if res.returncode != 0:
        return False
    mounts = json.loads(res.stdout)[0].get("Mounts", [])
    for m in mounts:
        if m.get("Destination") == "/usr/lib/python3/dist-packages/odoo":
            return True
    return False


def detect_odoo_dirs_via_python(exec_target: list[str]) -> list[str]:
    """
    Detecta directorios del paquete odoo usando importlib.
    Compatible con paquetes namespace.
    """
    code = r"""
import importlib.util
spec = importlib.util.find_spec("odoo")
locs = list((spec.submodule_search_locations or []))
print("\n".join(locs))
"""
    proc = run(exec_target + ["python3", "-c", code])
    if proc.returncode != 0:
        return []
    paths = [p.strip() for p in proc.stdout.splitlines() if p.strip()]
    return paths


def stream_tar_from_container(exec_target: list[str], src_dir: str, dest: Path, excludes: list[str] | None = None):
    """
    Copia archivos desde el contenedor usando tar stream (evita problemas con symlinks).
    """
    dest.mkdir(parents=True, exist_ok=True)

    tar_cmd = exec_target + ["tar", "-C", src_dir]
    excludes = excludes or []
    for pattern in excludes:
        tar_cmd.append(f"--exclude={pattern}")
    tar_cmd += ["-cf", "-", "."]

    proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    try:
        host_tar = subprocess.run(
            ["tar", "-xf", "-", "-C", str(dest), "--no-same-owner"],
            stdin=proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        proc.stdout.close()
        stderr_combined = (proc.stderr.read() or b"") + (host_tar.stderr or b"")
        ret = proc.wait()
        if ret != 0 or host_tar.returncode != 0:
            msg = stderr_combined.decode(errors="ignore").strip()
            raise click.ClickException(f"tar stream failed from {src_dir}: {msg or 'unknown error'}")
    finally:
        if proc.poll() is None:
            proc.terminate()


@click.group("odools", short_help="Odoo LS config utilities")
def odools():
    """Gestiona odools.toml y sincroniza el código Odoo para el LSP."""
    pass


@odools.command("init", short_help="Generate odools.toml with absolute paths (new [[config]] format)")
@click.option("--profile", default="main", show_default=True, help="Profile name to write.")
@click.option(
    "--with-autodetect/--no-autodetect",
    default=True,
    show_default=True,
    help="Include $autoDetectAddons in addons_paths.",
)
def init(profile: str, with_autodetect: bool):
    """
    Genera odools.toml en el formato nuevo compatible con odoo-ls 1.0.x
    usando rutas absolutas por defecto.
    """
    root = ensure_project_root()
    odoo_version = get_env("ODOO_VERSION")
    vendor_odoo = (root / "vendor" / f"odoo-{odoo_version}").resolve()
    src_dir = (root / "src").resolve()

    # siempre rutas absolutas
    odoo_path = str(vendor_odoo)
    main_addons = str(src_dir)

    python_path = shutil.which("python3") or "/usr/bin/python3"
    typeshed_root = Path.home() / ".local/share/nvim/odoo/typeshed"
    stdlib = str(typeshed_root / "stdlib")
    extra_stub = str(typeshed_root / "stubs")

    addons_paths = [main_addons]
    if with_autodetect:
        addons_paths.append("$autoDetectAddons")

    toml_lines = [
        "[[config]]",
        f'name = "{profile}"',
        f'odoo_path = "{odoo_path}"',
        "addons_paths = [",
    ]
    for item in addons_paths:
        toml_lines.append(f'  "{item}",')
    toml_lines.append("]")
    toml_lines.append(f'python_path = "{python_path}"')
    toml_lines.append("additional_stubs = [")
    toml_lines.append(f'  "{extra_stub}",')
    toml_lines.append("]")
    toml_lines.append(f'stdlib = "{stdlib}"')
    toml_lines.append("")  # newline final

    toml_path = root / "odools.toml"
    toml_path.write_text("\n".join(toml_lines), encoding="utf-8")
    click.echo(f"✅ Wrote {toml_path} with absolute paths and profile {profile!r}")


@odools.command("sync", short_help="Mirror Odoo code into ./vendor/odoo-<version>")
@click.option("--force", is_flag=True, help="Delete existing vendor/odoo-<version> before syncing.")
def sync(force):
    root = ensure_project_root()
    odoo_version = get_env("ODOO_VERSION")
    project_name = get_env("PROJECT_NAME")
    container = f"{project_name}_odoo"
    vendor_odoo = root / "vendor" / f"odoo-{odoo_version}"

    if vendor_odoo.exists():
        if force:
            shutil.rmtree(vendor_odoo)
        else:
            raise click.ClickException(f"{vendor_odoo} already exists. Use --force to overwrite.")
    vendor_odoo.mkdir(parents=True, exist_ok=True)

    excludes = ["addons/point_of_sale/static/src/fonts/Inconsolata.otf"]
    extra = os.getenv("ODOOLS_SYNC_EXCLUDES", "")
    if extra.strip():
        excludes.extend([e.strip() for e in extra.split(",") if e.strip()])

    overlay = has_vendor_mount(container)
    image_id = get_container_image_id(container)

    if overlay:
        click.echo("🔎 Detected vendor overlay mount in running container. Using a temporary container from the image.")
        temp_name = f"{project_name}_odoo_sync_tmp"
        _ = run(["docker", "rm", "-f", temp_name])
        _ = run(["docker", "create", "--name", temp_name, image_id, "sleep", "infinity"], check=True)
        try:
            odoo_dirs = detect_odoo_dirs_via_python(["docker", "exec", temp_name]) or ["/usr/lib/python3/dist-packages/odoo"]
            click.echo(f"📦 Copying Odoo from image ({image_id[:12]}): {', '.join(odoo_dirs)} → {vendor_odoo}")
            for src in odoo_dirs:
                stream_tar_from_container(["docker", "exec", temp_name], src, vendor_odoo, excludes=excludes)
        finally:
            _ = run(["docker", "rm", "-f", temp_name])
    else:
        odoo_dirs = detect_odoo_dirs_via_python(["docker", "exec", container]) or ["/usr/lib/python3/dist-packages/odoo"]
        click.echo(f"📦 Copying Odoo from {container}: {', '.join(odoo_dirs)} → {vendor_odoo}")
        for src in odoo_dirs:
            stream_tar_from_container(["docker", "exec", container], src, vendor_odoo, excludes=excludes)

    if not any((vendor_odoo / p).exists() for p in ("__init__.py", "addons")):
        raise click.ClickException(f"Sync ended but {vendor_odoo} looks empty. Expected '__init__.py' or 'addons/'.")
    click.echo("✅ Sync completed")


@odools.command("make", short_help="Run init then sync")
@click.option("--profile", default="main", show_default=True)
@click.option("--force", is_flag=True, help="Overwrite existing vendor/odoo-<version>.")
@click.option("--no-autodetect", is_flag=True, help="Skip adding $autoDetectAddons.")
def make(profile: str, force: bool, no_autodetect: bool):
    """Genera odools.toml y sincroniza vendor/."""
    init.callback(profile=profile, with_autodetect=not no_autodetect)
    sync.callback(force=force)

