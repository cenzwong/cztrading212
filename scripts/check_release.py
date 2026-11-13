"""Validate a release tag, tracked paths, and built distribution contents."""

from __future__ import annotations

import argparse
import email
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*\Z")
_TAG = re.compile(r"v[0-9][A-Za-z0-9.!+_-]*\Z")


def sensitive_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        lower = part.lower()
        if (
            lower == ".envrc"
            or lower == ".env"
            or (lower.startswith(".env.") and lower not in {".env.sample", ".env.example"})
        ):
            return True
        if lower in {"__pycache__", ".venv", ".git", ".aws"}:
            return True
        if lower.endswith(
            (
                ".sqlite",
                ".sqlite3",
                ".sqlite-wal",
                ".sqlite-shm",
                ".sqlite-journal",
                ".sqlite3-wal",
                ".sqlite3-shm",
                ".sqlite3-journal",
                ".db",
                ".db-wal",
                ".db-shm",
                ".db-journal",
                ".pem",
                ".key",
                ".p12",
                ".pfx",
            )
        ):
            return True
    return False


def validate_tracked_paths(root: Path) -> None:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout
    for path in output.decode("utf-8", errors="surrogateescape").split("\0"):
        if path and sensitive_path(path):
            raise ValueError(f"Sensitive or local-data path is tracked: {path}")


def validate_clean_checkout(root: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    if result.stdout:
        raise ValueError("Release checkout must have no tracked or untracked changes")


def validate_tag(root: Path, tag: str, *, require_main_ancestor: bool = True) -> str:
    with (root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    version = project["version"]
    if not isinstance(version, str) or not _TAG.fullmatch(tag) or tag != "v" + version:
        raise ValueError("Release tag must exactly match v plus the project version")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != commit:
        raise ValueError("The checked-out commit does not match the requested tag")
    if require_main_ancestor:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("Release tag must point to a commit reachable from origin/main")
    return version


def _metadata(data: bytes, version: str) -> None:
    message = email.message_from_bytes(data)
    name = message.get("Name", "")
    if not _NAME.fullmatch(name.lower()) or re.sub(r"[-_.]+", "-", name.lower()) != "cztrading212":
        raise ValueError("Distribution metadata has an unexpected package name")
    if message.get("Version") != version:
        raise ValueError("Distribution metadata version does not match the release tag")


def _validate_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or sensitive_path(name):
        raise ValueError(f"Unsafe or sensitive distribution path: {name}")


def validate_artifacts(directory: Path, version: str) -> None:
    archives = sorted(path for path in directory.iterdir() if path.name != ".gitignore")
    wheels = [p for p in archives if p.name.endswith(".whl")]
    sources = [p for p in archives if p.name.endswith(".tar.gz")]
    if len(archives) != 2 or len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Release must contain exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = wheel.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Wheel contains duplicate archive paths")
        for member in members:
            _validate_member(member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Wheel must not contain symlinks")
        required = {
            "cztrading212/__init__.py",
            "cztrading212/py.typed",
            "cztrading212/_generated/operations.json",
            "cztrading212/_generated/schemas.json",
            "cztrading212/_generated/parsers.py",
        }
        if not required <= set(names):
            raise ValueError("Wheel is missing required SDK files")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel must contain exactly one metadata file")
        _metadata(wheel.read(metadata[0]), version)
    with tarfile.open(sources[0], "r:gz") as source:
        members = source.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Source archive contains duplicate paths")
        for member in members:
            _validate_member(member.name)
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("Source archive contains an unsupported entry")
        metadata = [
            member
            for member in members
            if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
        ]
        if len(metadata) != 1:
            raise ValueError("Source archive must contain exactly one PKG-INFO")
        stream = source.extractfile(metadata[0])
        if stream is None:
            raise ValueError("Source metadata is unreadable")
        _metadata(stream.read(), version)
        for suffix in (
            "/pyproject.toml",
            "/src/cztrading212/__init__.py",
            "/openapi/upstream.json",
            "/scripts/generate.py",
        ):
            if not any(name.endswith(suffix) for name in names):
                raise ValueError("Source archive is missing required project files")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--source-only", action="store_true", help="Check tag and tracked paths before building"
    )
    args = parser.parse_args()
    validate_tracked_paths(ROOT)
    validate_clean_checkout(ROOT)
    version = validate_tag(ROOT, args.tag)
    if not args.source_only:
        validate_artifacts(args.dist, version)
    print(
        "Release source checks passed"
        if args.source_only
        else "Release source and distribution checks passed"
    )


if __name__ == "__main__":
    main()
