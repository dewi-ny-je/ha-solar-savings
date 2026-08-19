"""Tests for the release script that stamps the version into the repository."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
USAGE_ERROR = 2
MANIFEST = """{
  "domain": "solar_savings",
  "name": "Solar Savings",
  "after_dependencies": ["sensor"],
  "config_flow": true,
  "version": "0.6.0"
}
"""
PYPROJECT = """[build-system]
requires = ["setuptools>=69"]

[project]
name = "ha-solar-savings"
version = "0.5.0"
requires-python = ">=3.13"

[tool.ruff]
target-version = "py313"
"""


def load_script() -> ModuleType:
    """Import the release script, which lives outside any package."""
    path = ROOT / ".github" / "scripts" / "set_version.py"
    spec = importlib.util.spec_from_file_location("set_version", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


set_version = load_script()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a miniature repository and make it the working directory."""
    manifest = tmp_path / "custom_components" / "solar_savings" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read_versions(repo: Path) -> tuple[str, str]:
    """Return the versions the two files declare."""
    manifest = json.loads(
        (repo / "custom_components" / "solar_savings" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return manifest["version"], pyproject["project"]["version"]


def test_both_files_are_stamped(repo: Path) -> None:
    """A release writes the same version to the manifest and the metadata."""
    assert set_version.main(["1.1.2"]) == 0

    assert read_versions(repo) == ("1.1.2", "1.1.2")


def test_a_tag_name_is_accepted(repo: Path) -> None:
    """The workflow may pass either the version or the tag."""
    assert set_version.main(["v2.0.0"]) == 0

    assert read_versions(repo) == ("2.0.0", "2.0.0")


def test_only_the_version_lines_change(repo: Path) -> None:
    """Formatting, key order, and unrelated keys survive the rewrite."""
    assert set_version.main(["1.1.2"]) == 0

    manifest = (
        repo / "custom_components" / "solar_savings" / "manifest.json"
    ).read_text(encoding="utf-8")
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")

    assert manifest == MANIFEST.replace('"0.6.0"', '"1.1.2"')
    assert pyproject == PYPROJECT.replace('"0.5.0"', '"1.1.2"')


def test_the_ruff_target_version_is_left_alone(repo: Path) -> None:
    """Only the version of the [project] table is a release version."""
    assert set_version.main(["1.1.2"]) == 0

    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["ruff"]["target-version"] == "py313"


def test_stamping_twice_is_a_no_op(repo: Path) -> None:
    """Re-running on an already stamped checkout leaves the files untouched."""
    assert set_version.main(["1.1.2"]) == 0
    stamped = (repo / "pyproject.toml").read_text(encoding="utf-8")

    assert set_version.main(["1.1.2"]) == 0

    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == stamped
    assert read_versions(repo) == ("1.1.2", "1.1.2")


def test_a_missing_pyproject_is_not_required(repo: Path) -> None:
    """The manifest is the only file a Home Assistant repository must have."""
    (repo / "pyproject.toml").unlink()

    assert set_version.main(["1.1.2"]) == 0

    manifest = json.loads(
        (repo / "custom_components" / "solar_savings" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["version"] == "1.1.2"


def test_a_version_without_a_project_table_is_rejected(repo: Path) -> None:
    """A version outside [project] is not the release version."""
    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nversion = "0.5.0"\n', encoding="utf-8"
    )

    with pytest.raises(SystemExit):
        set_version.main(["1.1.2"])


def test_a_malformed_version_is_rejected(repo: Path) -> None:
    """Anything which is not MAJOR.MINOR.PATCH fails before a file is touched."""
    assert set_version.main(["1.1"]) == 1

    assert read_versions(repo) == ("0.6.0", "0.5.0")


def test_the_usage_message_needs_one_argument(repo: Path) -> None:
    """Calling the script without a version is a usage error."""
    assert set_version.main([]) == USAGE_ERROR

    assert read_versions(repo) == ("0.6.0", "0.5.0")
