"""Validated host skill sources; never copy or maintain container-owned files."""

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import OdxError


@dataclass(frozen=True)
class SkillSource:
    path: Path
    # A single directory skill needs a virtual parent to retain its path-derived ID.
    directory: str | None = None

    def validate(self):
        try:
            if self.path.resolve(strict=True) != self.path or not self.path.is_dir():
                raise OdxError(f"Skill source is not a canonical directory: {self.path}")
            config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            if self.path in {
                Path(self.path.anchor),
                Path.home().resolve(),
                (config_home / "opencode").resolve(),
            } or any(
                (self.path / name).exists()
                for name in ("opencode.json", "opencode.jsonc", "cli.json", "auth.json")
            ):
                raise OdxError(f"Select only a skills directory, not a host profile: {self.path}")

            ids = set()

            def visit(directory, ancestors):
                canonical = directory.resolve(strict=True)
                if canonical in ancestors:
                    raise OdxError(f"Cyclic directory link in skill source: {directory}")
                for child in sorted(directory.iterdir()):
                    if child.is_symlink():
                        target = child.readlink()
                        if target.is_absolute():
                            raise OdxError(f"Absolute links are not portable in skills: {child}")
                        location = child.parent
                        for part in target.parts:
                            location = location.parent if part == ".." else location / part
                            if not location.is_relative_to(self.path):
                                raise OdxError(f"Skill link escapes its source: {child}")
                        resolved = child.resolve(strict=True)
                        if not resolved.is_relative_to(self.path):
                            raise OdxError(f"Skill link escapes its source: {child}")
                    if child.is_dir():
                        visit(child, ancestors | {canonical})
                    elif child.is_file():
                        skill_id = None
                        if child.name == "SKILL.md":
                            skill_id = self.directory if directory == self.path else directory.name
                        elif (
                            self.directory is None
                            and directory == self.path
                            and child.suffix == ".md"
                        ):
                            skill_id = child.stem
                        if skill_id is not None:
                            if skill_id in ids:
                                raise OdxError(
                                    f"Duplicate skill ID {skill_id!r} within source: {self.path}"
                                )
                            ids.add(skill_id)
                    else:
                        raise OdxError(
                            f"Skill sources must contain only files/directories: {child}"
                        )

            visit(self.path, set())
            if not ids:
                raise OdxError(f"No skills found in source: {self.path}")
            if (self.path / "SKILL.md").is_file() != (self.directory is not None):
                raise OdxError(f"Skill source layout changed; reload configuration: {self.path}")
        except (OSError, RuntimeError) as error:
            raise OdxError(f"Cannot read skill source {self.path}: {error}") from error


def skill_sources(root, entries):
    if not isinstance(entries, list):
        raise OdxError("local opencode.skill_sources must be an array of directory paths")
    result = []
    for value in entries:
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise OdxError("local opencode.skill_sources entries must be non-empty directory paths")
        try:
            configured = root / Path(value).expanduser()
            path = configured.resolve(strict=True)
            name = configured.name if configured.name not in {"", ".."} else path.name
            source = SkillSource(path, name if (path / "SKILL.md").is_file() else None)
            source.validate()
        except (OSError, RuntimeError) as error:
            raise OdxError(f"Cannot resolve skill source {value}: {error}") from error
        if any(
            path.is_relative_to(other.path) or other.path.is_relative_to(path) for other in result
        ):
            raise OdxError("Duplicate or overlapping OpenCode skill sources")
        result.append(source)
    return tuple(result)
