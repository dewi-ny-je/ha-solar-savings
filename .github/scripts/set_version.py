#!/usr/bin/env python3
"""Write a released version into every file that declares one.

Two files carry the version and they have to agree: the integration manifest,
which is what HACS and Home Assistant report for an installed copy, and the
packaging metadata in ``pyproject.toml``.

Both files are edited in place rather than rewritten from parsed data, so
formatting, key order, and comments survive untouched.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
MANIFEST_VERSION = re.compile(r'("version"\s*:\s*")[^"]*(")')
TABLE_HEADER = re.compile(r"^\[", re.MULTILINE)
PROJECT_TABLE = re.compile(r"^\[project\]\s*$", re.MULTILINE)
PYPROJECT_VERSION = re.compile(r'^(version\s*=\s*")[^"]*(")', re.MULTILINE)


def manifest_path() -> Path | None:
    """Return the path of the integration manifest."""
    return next(iter(sorted(Path("custom_components").glob("*/manifest.json"))), None)


def set_manifest_version(path: Path, version: str) -> bool:
    """Point the integration manifest at a version.

    Returns whether the file changed.
    """
    text = path.read_text(encoding="utf-8")
    if json.loads(text).get("version") == version:
        return False

    updated, count = MANIFEST_VERSION.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        message = f"{path} declares no version to update."
        raise SystemExit(message)

    if json.loads(updated).get("version") != version:
        message = f"Updating the version in {path} did not take effect."
        raise SystemExit(message)

    path.write_text(updated, encoding="utf-8")
    return True


def set_pyproject_version(path: Path, version: str) -> bool:
    """Point the packaging metadata at a version.

    Only the ``version`` key of the ``[project]`` table is touched: the same
    key means something else under, say, ``[tool.ruff]``. Returns whether the
    file changed.
    """
    text = path.read_text(encoding="utf-8")
    project = tomllib.loads(text).get("project", {})
    if project.get("version") == version:
        return False

    table = PROJECT_TABLE.search(text)
    if table is None:
        message = f"{path} has no [project] table to update."
        raise SystemExit(message)

    end = TABLE_HEADER.search(text, table.end())
    stop = end.start() if end else len(text)

    body, count = PYPROJECT_VERSION.subn(
        rf"\g<1>{version}\g<2>", text[table.end() : stop], count=1
    )
    if count != 1:
        message = f"The [project] table in {path} declares no version to update."
        raise SystemExit(message)

    updated = text[: table.end()] + body + text[stop:]
    if tomllib.loads(updated)["project"]["version"] != version:
        message = f"Updating the version in {path} did not take effect."
        raise SystemExit(message)

    path.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    """Write the version passed on the command line into both files."""
    if len(argv) != 1:
        print(f"usage: {Path(__file__).name} MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2

    version = argv[0].lstrip("v")
    if not VERSION.match(version):
        print(f"'{argv[0]}' is not a MAJOR.MINOR.PATCH version.", file=sys.stderr)
        return 1

    if (manifest := manifest_path()) is None:
        print("No integration manifest found under custom_components.", file=sys.stderr)
        return 1

    targets: list[tuple[Path, bool]] = [
        (manifest, set_manifest_version(manifest, version))
    ]
    pyproject = Path("pyproject.toml")
    if pyproject.is_file():
        targets.append((pyproject, set_pyproject_version(pyproject, version)))

    for path, changed in targets:
        print(f"{path}: {'set to' if changed else 'already at'} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
