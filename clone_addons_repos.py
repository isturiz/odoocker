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
from dataclasses import dataclass
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


def load_config(path: Path) -> Dict[str, List[RepoSpec]]:
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
            repos.append(RepoSpec(section=section, name=name, url=url, branch=branch))

        if repos:
            config[section] = repos

    return config


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


def clone_or_update_repo(base_dir: Path, spec: RepoSpec) -> None:
    target_root = base_dir / spec.section
    ensure_dir(target_root)

    target_dir = target_root / spec.name

    if target_dir.is_dir():
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
            
            # Buscar líneas de continuación
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
                    # Si no termina en \, es la última línea de continuación
                    if not cont_line.endswith("\\"):
                        j += 1
                        break
                    j += 1
                else:
                    # No es continuación, terminar
                    break
            end_line = j
            break
    
    return paths, start_line, end_line


def update_odoo_conf_addons_path(conf_path: Path, repo_paths: list[str]) -> bool:
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

    # Solo añadir rutas faltantes, respetando el orden de repo_paths
    missing_paths = [path for path in repo_paths if path not in paths]

    # Detectar si el bloque actual usa backslashes o formato que conviene normalizar
    lines = content.splitlines()
    current_block = lines[start_line:end_line]
    needs_format_fix = any("\\" in line for line in current_block)

    if not missing_paths and not needs_format_fix:
        print(f"\naddons_path en {conf_path} ya contiene todas las rutas necesarias.")
        return False

    paths.extend(missing_paths)

    # Reconstruir el contenido con formato multilínea sin usar backslashes,
    # dejando cada ruta separada por comas para que configparser la procese bien.
    new_lines = lines[:start_line]

    for i, path in enumerate(paths):
        suffix = "," if i < len(paths) - 1 else ""
        line_value = f"{path}{suffix}"
        if i == 0:
            new_lines.append(f"addons_path = {line_value}")
        else:
            new_lines.append(f" {line_value}")

    new_lines.extend(lines[end_line:])

    conf_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    if missing_paths:
        print(f"\n✓ Actualizado {conf_path}: añadidas rutas {', '.join(missing_paths)}")
    else:
        print(f"\n✓ Normalizado addons_path en {conf_path}")
    return True


def build_repo_paths(base_dir: Path, config: Dict[str, List[RepoSpec]]) -> list[str]:
    """Devuelve rutas completas a cada repo configurado, sin duplicados."""
    repo_paths: list[str] = []
    seen: set[str] = set()
    for section, repos in config.items():
        for spec in repos:
            path = str(base_dir / section / spec.name)
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

    config = load_config(config_path)
    if not config:
        print("No se encontraron repositorios en la configuración.")
        return 0

    for section, repos in config.items():
        print(f"\n=== Sección: {section} ===")
        for spec in repos:
            clone_or_update_repo(base_dir, spec)

    # Actualizar odoo.conf con rutas de cada repo clonado
    repo_paths = build_repo_paths(base_dir, config)
    if repo_paths:
        script_dir = Path(__file__).resolve().parent
        odoo_conf_path = script_dir / ODOO_CONF_DEFAULT
        update_odoo_conf_addons_path(odoo_conf_path, repo_paths)

    print("\nProceso terminado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

