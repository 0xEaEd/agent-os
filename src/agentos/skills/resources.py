"""Resource directory support for skills — scripts, references, assets."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

_RESOURCE_DIRS = ("references", "scripts", "assets", "templates")

# A SKILL.md is written before anyone knows where it will be installed — bundled
# under site-packages on one machine, under a workspace directory on the next —
# so it names its own files through this placeholder instead of a real path.
SKILL_BASE_DIR_PLACEHOLDER = "{baseDir}"

# Likewise for the interpreter. A skill script needs the Python that AgentOS
# itself runs on: that is the one guaranteed to have the skill's dependencies
# and to be new enough for its syntax. A bare ``python`` in a shell command is
# whatever the user's PATH says — a Homebrew 3.9 with a stray ``httpx``, or
# nothing at all on Windows — and every bundled script used to inherit that.
SKILL_PYTHON_PLACEHOLDER = "{python}"


def skill_python() -> str:
    """Return the interpreter a skill script should run under, shell-quoted.

    ``sys.executable`` of the gateway process. Wrapped in double quotes when
    the path carries whitespace so it survives both ``sh`` and ``cmd``.
    """
    executable = sys.executable or "python"
    if any(ch.isspace() for ch in executable):
        return f'"{executable}"'
    return executable


def expand_skill_placeholders(text: str, base_dir: str, python: str | None = None) -> str:
    """Resolve ``{baseDir}`` and ``{python}`` in a skill body.

    ``{baseDir}`` becomes the skill's install directory and ``{python}`` the
    interpreter AgentOS runs on (``python`` overrides it, for tests).

    Only ever called on the copy handed to the model. Expanding at load time
    would put a machine-specific absolute path into ``SkillSpec.content``, which
    ``skill_edit`` writes back to disk when it is asked to change frontmatter
    and not the body.
    """
    if not text:
        return text
    text = text.replace(SKILL_PYTHON_PLACEHOLDER, python or skill_python())
    if not base_dir:
        return text
    return text.replace(SKILL_BASE_DIR_PLACEHOLDER, base_dir)


class SkillResources:
    """Access skill resource directories: scripts/, references/, assets/."""

    def __init__(self, skill_dir: Path) -> None:
        self._dir = skill_dir

    @property
    def scripts_dir(self) -> Path:
        return self._dir / "scripts"

    @property
    def references_dir(self) -> Path:
        return self._dir / "references"

    @property
    def assets_dir(self) -> Path:
        return self._dir / "assets"

    @property
    def templates_dir(self) -> Path:
        return self._dir / "templates"

    def has_scripts(self) -> bool:
        return self.scripts_dir.is_dir() and any(self.scripts_dir.iterdir())

    def has_references(self) -> bool:
        return self.references_dir.is_dir() and any(self.references_dir.iterdir())

    def has_assets(self) -> bool:
        return self.assets_dir.is_dir() and any(self.assets_dir.iterdir())

    def list_scripts(self) -> list[Path]:
        if not self.scripts_dir.is_dir():
            return []
        return sorted(p for p in self.scripts_dir.iterdir() if p.is_file())

    def list_references(self) -> list[Path]:
        if not self.references_dir.is_dir():
            return []
        return sorted(p for p in self.references_dir.iterdir() if p.is_file())

    def list_assets(self) -> list[Path]:
        if not self.assets_dir.is_dir():
            return []
        return sorted(p for p in self.assets_dir.iterdir() if p.is_file())

    def read_resource(self, name: str) -> str | None:
        """Read a text resource by skill-relative path.

        Supports SKILL.md plus package resource paths such as
        ``references/foo.md``, ``scripts/tool.py``, ``assets/palette.txt``,
        and ``templates/report.md``. Bare filenames keep the legacy lookup
        behavior by trying references/, scripts/, assets/, then templates/.
        """

        relative = _normalise_resource_path(name)
        if relative is None:
            return None

        parts = relative.parts
        if parts == ("SKILL.md",):
            return self._read_text_under(self._dir, relative)

        if parts and parts[0] in _RESOURCE_DIRS:
            root = self._dir / parts[0]
            return self._read_text_under(root, Path(*parts[1:]))

        if len(parts) == 1:
            for root_name in _RESOURCE_DIRS:
                content = self._read_text_under(self._dir / root_name, relative)
                if content is not None:
                    return content
        return None

    def read_reference(self, name: str) -> str | None:
        """Read a reference file by name. Returns None if not found."""
        relative = _normalise_resource_path(name)
        if relative is None:
            return None
        if relative.parts[:1] == ("references",):
            relative = Path(*relative.parts[1:])
        return self._read_text_under(self.references_dir, relative)

    def read_script(self, name: str) -> str | None:
        """Read a script file by name. Returns None if not found."""
        relative = _normalise_resource_path(name)
        if relative is None:
            return None
        if relative.parts[:1] == ("scripts",):
            relative = Path(*relative.parts[1:])
        return self._read_text_under(self.scripts_dir, relative)

    def _read_text_under(self, root: Path, relative: Path) -> str | None:
        if not relative.parts:
            return None
        path = root / relative
        try:
            resolved_root = root.resolve()
            resolved_path = path.resolve()
            resolved_path.relative_to(resolved_root)
        except (OSError, ValueError):
            return None
        if not resolved_path.is_file():
            return None
        try:
            return resolved_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None


def _normalise_resource_path(name: str) -> Path | None:
    raw = name.strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None

    posix = PurePosixPath(raw)
    if posix.is_absolute():
        return None
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return Path(*parts)
