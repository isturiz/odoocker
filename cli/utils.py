import subprocess
import os
from pathlib import Path
from dotenv import load_dotenv


def container_path_exists(container, path):
    """
    Check if a directory exists at the specified path within a Docker container.
    """
    result = subprocess.run(
        ["docker", "exec", container, "test", "-d", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0


def find_project_root(marker_file="compose.yaml"):
    current_dir = Path.cwd()
    home_dir = Path.home()
    while current_dir != home_dir.parent:
        if (current_dir / marker_file).exists():
            env_path = current_dir / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=env_path)
            return current_dir
        if current_dir == home_dir:
            break
        current_dir = current_dir.parent
    return None


def get_project_name(default="odoo"):
    return os.getenv("PROJECT_NAME", default)
