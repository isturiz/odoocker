#!/usr/bin/env python3
"""
Script para clonar repositorios de addons de Odoo en una estructura:

addons/
  third-party/
  oca/
  custom/

La configuración de los repos se define en `addons_repos.toml` en la raíz del
proyecto (junto a este script, por defecto).

Formato del fichero TOML:

[[third-party]]
name = "nombre_carpeta_local"   # opcional; si no se indica, se usa el nombre del repo
url = "https://github.com/organizacion/repositorio.git"
branch = "16.0"                 # opcional; si no se indica, se usa la rama por defecto

Las mismas claves aplican para [[oca]] y [[custom]].

Variables de entorno opcionales:
  ADDONS_BASE_DIR   -> ruta base donde crear `third-party/`, `oca/`, `custom/`
                       (por defecto: /workspace/addons)
  ADDONS_CONFIG     -> ruta alternativa al fichero addons_repos.toml
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional


CONFIG_DEFAULT = "addons_repos.toml"
DEFAULT_BASE_DIR = "/workspace/addons"
ODOO_CONF_DEFAULT = "odoo.conf"
SECTIONS = ("third-party", "oca", "custom")


@dataclass
class RepoSpec:
    section: str
    name: str
    url: str
    branch: Optional[str] = None
    subpath: Optional[str] = None


@dataclass
class ConfigOptions:
    preserve_addons_path: list[str] = field(default_factory=list)


def load_config(path: Path) -> tuple[Dict[str, List[RepoSpec]], ConfigOptions]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError as exc:  # pragma: no cover - entorno antiguo
        raise SystemExit("Python >= 3.11 es requerido (módulo tomllib no disponible).") from exc

    if not path.is_file():
        raise SystemExit(f"Fichero de configuración no encontrado: {path}")

    with path.open("rb") as f:
        raw = tomllib.load(f)

    config: Dict[str, List[RepoSpec]] = {}
    for section in SECTIONS:
        entries = raw.get(section, [])
        if not isinstance(entries, list):
            continue

        repos: List[RepoSpec] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            url = entry.get("url")
            if not url:
                continue

            name = entry.get("name")
            if not name:
                # Derivar nombre del repo a partir de la URL
                name = derive_name_from_url(url)

            branch = entry.get("branch")
            subpath = entry.get("subpath")
            repos.append(RepoSpec(section=section, name=name, url=url, branch=branch, subpath=subpath))

        if repos:
            config[section] = repos

    options_raw = raw.get("options", {})
    preserve_addons_path: list[str] = []
    if isinstance(options_raw, dict):
        value = options_raw.get("preserve_addons_path", [])
        if isinstance(value, str):
            preserve_addons_path = [value]
        elif isinstance(value, list):
            preserve_addons_path = [item for item in value if isinstance(item, str)]

    return config, ConfigOptions(preserve_addons_path=preserve_addons_path)


def derive_name_from_url(url: str) -> str:
    # coge la última parte del path, sin .git
    part = url.rstrip("/").split("/")[-1]
    if part.endswith(".git"):
        part = part[:-4]
    return part


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: Iterable[str], cwd: Optional[Path] = None) -> int:
    print(f"Ejecutando: {' '.join(cmd)}", flush=True)
    try:
        result = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            check=False,
        )
        return result.returncode
    except FileNotFoundError:
        raise SystemExit("Error: `git` no está instalado o no se encuentra en PATH.")


def is_git_repo(path: Path) -> bool:
    git_entry = path / ".git"
    return path.is_dir() and git_entry.exists()


def clone_or_update_repo(base_dir: Path, spec: RepoSpec) -> bool:
    target_root = base_dir / spec.section
    ensure_dir(target_root)

    target_dir = target_root / spec.name

    if target_dir.is_dir():
        if not is_git_repo(target_dir):
            print(f"\nAviso: {target_dir} existe pero no es un repo git, se omite.")
            return False
        print(f"\nRepositorio ya existe, actualizando: {target_dir}")
        # git fetch y checkout de rama (si se especifica)
        rc = run_cmd(["git", "fetch", "--all", "--prune"], cwd=target_dir)
        if rc != 0:
            print(f"  Aviso: fallo al hacer fetch en {target_dir} (código {rc})")
        if spec.branch:
            rc = run_cmd(["git", "checkout", spec.branch], cwd=target_dir)
            if rc != 0:
                print(f"  Aviso: fallo al hacer checkout de rama {spec.branch} (código {rc})")
    else:
        print(f"\nClonando repositorio en {target_dir}")
        cmd = ["git", "clone"]
        if spec.branch:
            cmd += ["--branch", spec.branch, "--single-branch"]
        cmd += [spec.url, str(target_dir)]
        rc = run_cmd(cmd)
        if rc != 0:
            print(f"  Error al clonar {spec.url} (código {rc})")
            return False
        if not is_git_repo(target_dir):
            print(f"  Error: {target_dir} no parece un repo git tras clonar.")
            return False
    return True


def parse_addons_path(content: str) -> tuple[list[str], int, int]:
    """
    Parsea el addons_path del contenido del archivo odoo.conf.
    Retorna: (lista de paths, línea de inicio, línea de fin)
    """
    lines = content.splitlines()
    paths: list[str] = []
    start_line = -1
    end_line = -1
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("addons_path"):
            start_line = i
            # Extraer el valor (puede estar en la misma línea o continuar)
            if "=" in stripped:
                value_part = stripped.split("=", 1)[1].strip()
                # Limpiar backslashes y espacios
                value_part = value_part.rstrip("\\").strip()
                if value_part:
                    # Separar por comas
                    for path in value_part.split(","):
                        path = path.strip().rstrip("\\").strip()
                        if path:
                            paths.append(path)
            
            # Buscar líneas de continuación (mientras estén indentadas)
            j = i + 1
            while j < len(lines):
                line_raw = lines[j]
                cont_line = line_raw.strip()

                # Si es línea vacía o comentario, continuar (puede haber líneas vacías en medio)
                if not cont_line or cont_line.startswith("#"):
                    j += 1
                    continue

                # Si empieza con espacio o tab, es continuación
                if line_raw.startswith((" ", "\t")):
                    # Limpiar backslashes y espacios
                    cont_clean = cont_line.rstrip("\\").strip()
                    if cont_clean:
                        # Separar por comas
                        for path in cont_clean.split(","):
                            path = path.strip().rstrip("\\").strip()
                            if path:
                                paths.append(path)
                    j += 1
                    continue

                # No es continuación, terminar
                break
            end_line = j
            break
    
    return paths, start_line, end_line


def update_odoo_conf_addons_path(
    conf_path: Path,
    repo_paths: list[str],
    preserve_addons_path: list[str],
) -> bool:
    """
    Actualiza el addons_path en odoo.conf añadiendo las rutas de cada repo clonado.
    Retorna True si se hizo algún cambio.
    """
    if not conf_path.is_file():
        print(f"\nAviso: {conf_path} no encontrado, no se actualizará addons_path.")
        return False

    content = conf_path.read_text(encoding="utf-8")
    paths, start_line, end_line = parse_addons_path(content)

    if start_line == -1:
        print(f"\nAviso: No se encontró 'addons_path' en {conf_path}.")
        return False

    lines = content.splitlines()

    preserved: list[str] = []
    if preserve_addons_path:
        for path in paths:
            if path in preserved:
                continue
            if any(path == keep for keep in preserve_addons_path):
                preserved.append(path)

    new_paths: list[str] = []
    for path in preserved + repo_paths:
        if path in new_paths:
            continue
        new_paths.append(path)

    if new_paths == paths:
        print(f"\naddons_path en {conf_path} ya contiene todas las rutas necesarias.")
        return False

    # Reconstruir el contenido con formato multilínea sin usar backslashes,
    # dejando cada ruta separada por comas para que configparser la procese bien.
    new_lines = lines[:start_line]

    for i, path in enumerate(new_paths):
        suffix = "," if i < len(new_paths) - 1 else ""
        line_value = f"{path}{suffix}"
        if i == 0:
            new_lines.append(f"addons_path = {line_value}")
        else:
            new_lines.append(f" {line_value}")

    new_lines.extend(lines[end_line:])

    conf_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n✓ Actualizado addons_path en {conf_path}")
    return True


def build_repo_paths(base_dir: Path, config: Dict[str, List[RepoSpec]]) -> list[str]:
    """Devuelve rutas completas a cada repo configurado, sin duplicados."""
    repo_paths: list[str] = []
    seen: set[str] = set()
    for section, repos in config.items():
        for spec in repos:
            base_path = base_dir / section / spec.name
            if spec.subpath:
                path = str(base_path / spec.subpath)
            else:
                path = str(base_path)
            if path in seen:
                continue
            seen.add(path)
            repo_paths.append(path)
    return repo_paths


def main(argv: List[str]) -> int:
    base_dir_env = os.environ.get("ADDONS_BASE_DIR", DEFAULT_BASE_DIR)
    config_env = os.environ.get("ADDONS_CONFIG")

    script_dir = Path(__file__).resolve().parent
    config_path = Path(config_env) if config_env else script_dir / CONFIG_DEFAULT
    base_dir = Path(base_dir_env)

    print(f"Usando base de addons: {base_dir}")
    print(f"Usando configuración: {config_path}")

    ensure_dir(base_dir)

    config, options = load_config(config_path)
    if not config:
        print("No se encontraron repositorios en la configuración.")
        return 0

    repo_paths: list[str] = []
    seen_paths: set[str] = set()
    for section, repos in config.items():
        print(f"\n=== Sección: {section} ===")
        for spec in repos:
            ok = clone_or_update_repo(base_dir, spec)
            if not ok:
                continue
            base_path = base_dir / section / spec.name
            if spec.subpath:
                repo_path = str(base_path / spec.subpath)
            else:
                repo_path = str(base_path)
            if repo_path in seen_paths:
                continue
            seen_paths.add(repo_path)
            repo_paths.append(repo_path)

    # Actualizar odoo.conf con rutas de cada repo clonado
    if repo_paths:
        script_dir = Path(__file__).resolve().parent
        odoo_conf_path = script_dir / ODOO_CONF_DEFAULT
        update_odoo_conf_addons_path(
            odoo_conf_path,
            repo_paths,
            options.preserve_addons_path,
        )

    print("\nProceso terminado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


