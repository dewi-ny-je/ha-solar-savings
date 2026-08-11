#!/usr/bin/env python3
"""Work out the next release version from the commits since the last release.

Commit subjects are read as conventional commits (``type(scope)!: description``)
and decide how the version is bumped:

* a ``!`` after the type, or a ``BREAKING CHANGE:`` footer, bumps the major
* ``feat`` bumps the minor
* every other type, and any subject which is not a conventional commit, bumps
  the patch

Merge commits are ignored, the commits they bring in are classified instead.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUBJECT = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?: *(?P<description>.+)$"
)
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
VERSION_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

MINOR_TYPES = {"feat"}

# Type to changelog section, in the order the sections are rendered.
SECTIONS: list[tuple[str, str]] = [
    ("breaking", "Breaking changes"),
    ("feat", "Features"),
    ("fix", "Bug fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "Continuous integration"),
    ("chore", "Chores"),
    ("other", "Other changes"),
]
KNOWN_TYPES = {key for key, _ in SECTIONS}

# Separators git writes through its %x escapes, so they never appear in argv.
# They must not be characters str.strip() considers whitespace, which rules out
# the \x1c to \x1f separators.
RECORD = "\x00"
FIELD = "\x01"


def git(*args: str) -> str:
    """Run a git command and return its raw output."""
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def previous_tag() -> str | None:
    """Return the most recent release tag reachable from HEAD."""
    tags = git("tag", "--list", "--merged", "HEAD", "--sort=-v:refname").split()
    return next((tag for tag in tags if VERSION_TAG.match(tag)), None)


def manifest_path() -> Path | None:
    """Return the path of the integration manifest."""
    return next(iter(sorted(Path("custom_components").glob("*/manifest.json"))), None)


def manifest_version() -> str | None:
    """Return the version the integration manifest declares, if it is usable."""
    if (path := manifest_path()) is None:
        return None
    version = json.loads(path.read_text(encoding="utf-8")).get("version", "")
    return version if VERSION_TAG.match(version) else None


def commits(since: str | None) -> list[tuple[str, str, str]]:
    """Return the (sha, subject, body) of every non merge commit since a tag."""
    output = git(
        "log",
        "--no-merges",
        "--format=%x00%H%x01%s%x01%b",
        f"{since}..HEAD" if since else "HEAD",
    )
    parsed = []
    for record in output.split(RECORD):
        if not record.strip():
            continue
        fields = record.split(FIELD)
        sha, subject = fields[0], fields[1]
        body = fields[2] if len(fields) > 2 else ""
        parsed.append((sha.strip(), subject.strip(), body.strip()))
    return parsed


def classify(subject: str, body: str) -> tuple[str, str]:
    """Return the changelog section and the description of a commit."""
    match = SUBJECT.match(subject)
    if match is None:
        return "other", subject
    if match["breaking"] or BREAKING_FOOTER.search(body):
        return "breaking", subject
    commit_type = match["type"].lower()
    return commit_type if commit_type in KNOWN_TYPES else "other", subject


def bump_for(sections: set[str]) -> str:
    """Return the version part to bump for the sections that were seen."""
    if "breaking" in sections:
        return "major"
    if sections & MINOR_TYPES:
        return "minor"
    return "patch"


def apply_bump(version: str, bump: str) -> str:
    """Return the version that results from bumping a version."""
    major, minor, patch = (int(part) for part in VERSION_TAG.match(version).groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def render_notes(
    grouped: dict[str, list[tuple[str, str]]], previous: str | None, tag: str
) -> str:
    """Render the release notes for the classified commits."""
    lines: list[str] = []
    for key, title in SECTIONS:
        if not grouped.get(key):
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(f"* {description} ({sha[:7]})" for sha, description in grouped[key])
        lines.append("")

    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository and previous:
        lines.append(
            f"**Full changelog**: "
            f"https://github.com/{repository}/compare/{previous}...{tag}"
        )
        lines.append("")
    return "\n".join(lines)


def write_output(**values: str) -> None:
    """Write the step outputs, both to GITHUB_OUTPUT and to stdout."""
    for name, value in values.items():
        print(f"{name}={value}")
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_file).open("a", encoding="utf-8") as file:
            for name, value in values.items():
                file.write(f"{name}={value}\n")


def main() -> int:
    """Work out the next version and write the step outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bump",
        choices=["auto", "major", "minor", "patch"],
        default="auto",
        help="override the bump computed from the commit messages",
    )
    parser.add_argument(
        "--initial-version",
        help="version to release when there is neither a release tag nor a "
        "version in the integration manifest",
    )
    parser.add_argument(
        "--notes-file",
        default="release-notes.md",
        help="file the release notes are written to",
    )
    args = parser.parse_args()

    previous = previous_tag()
    history = commits(previous)

    if not history:
        print(f"No commits since {previous}, nothing to release.", file=sys.stderr)
        write_output(release="false")
        return 0

    grouped: dict[str, list[tuple[str, str]]] = {}
    for sha, subject, body in history:
        section, description = classify(subject, body)
        grouped.setdefault(section, []).append((sha, description))

    # Without a release tag the version the integration declares is the
    # baseline, it is what a HACS download currently reports.
    baseline = previous or manifest_version()

    if baseline is None:
        if not args.initial_version:
            print(
                "The repository has no release tag and the manifest declares no "
                "version, so there is nothing to bump. Run this workflow manually "
                "with an initial version, or push the baseline tag yourself, for "
                "example `git tag v2.3.0`.",
                file=sys.stderr,
            )
            write_output(release="false")
            return 0
        if not VERSION_TAG.match(args.initial_version):
            print(
                f"'{args.initial_version}' is not a MAJOR.MINOR.PATCH version.",
                file=sys.stderr,
            )
            return 1
        bump = "initial"
        version = args.initial_version.lstrip("v")
    else:
        bump = args.bump if args.bump != "auto" else bump_for(set(grouped))
        version = apply_bump(baseline.lstrip("v"), bump)

    tag = f"v{version}"
    Path(args.notes_file).write_text(
        render_notes(grouped, previous, tag), encoding="utf-8"
    )

    write_output(
        release="true",
        version=version,
        tag=tag,
        bump=bump,
        previous_tag=previous or "",
        baseline=baseline or "",
        commits=str(len(history)),
        notes_file=args.notes_file,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
