import importlib.util
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = load_script("check_release")
repro = load_script("check_reproducible")
METADATA = b"Metadata-Version: 2.3\nName: cztrading212\nVersion: 0.1.0a1\n\n"


def make_archives(directory, *, wheel_extra=None, source_extra=None, version="0.1.0a1"):
    directory.mkdir(exist_ok=True)
    metadata = METADATA.replace(b"0.1.0a1", version.encode())
    wheel = directory / "cztrading212-0.1.0a1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in (
            "cztrading212/__init__.py",
            "cztrading212/py.typed",
            "cztrading212/_generated/operations.json",
            "cztrading212/_generated/schemas.json",
            "cztrading212/_generated/parsers.py",
        ):
            archive.writestr(name, b"")
        archive.writestr("cztrading212-0.1.0a1.dist-info/METADATA", metadata)
        if wheel_extra:
            archive.writestr(wheel_extra, b"test-placeholder")
    source = directory / "cztrading212-0.1.0a1.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        files = {
            "pkg/PKG-INFO": metadata,
            "pkg/pyproject.toml": b"",
            "pkg/src/cztrading212/__init__.py": b"",
            "pkg/openapi/upstream.json": b"",
            "pkg/scripts/generate.py": b"",
        }
        if source_extra:
            files[source_extra] = b"test-placeholder"
        for name, contents in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))


def test_release_artifact_allowance_and_secret_path_rejection(tmp_path):
    good = tmp_path / "good"
    make_archives(good)
    (good / ".gitignore").write_text("*\n")
    release.validate_artifacts(good, "0.1.0a1")
    with pytest.raises(ValueError, match="version"):
        release.validate_artifacts(good, "0.2.0")
    bad_wheel = tmp_path / "bad-wheel"
    make_archives(bad_wheel, wheel_extra="cztrading212/.env")
    with pytest.raises(ValueError, match="sensitive"):
        release.validate_artifacts(bad_wheel, "0.1.0a1")
    bad_source = tmp_path / "bad-source"
    make_archives(bad_source, source_extra="pkg/private/orders.sqlite")
    with pytest.raises(ValueError, match="sensitive"):
        release.validate_artifacts(bad_source, "0.1.0a1")
    traversal = tmp_path / "traversal"
    make_archives(traversal, source_extra="pkg/../escape")
    with pytest.raises(ValueError, match="Unsafe"):
        release.validate_artifacts(traversal, "0.1.0a1")


def test_tag_and_tracked_secret_checks_use_git_evidence(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("init", "-b", "main")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="cztrading212"\nversion="0.1.0a1"\n')
    git("add", "pyproject.toml")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "test")
    git("tag", "v0.1.0a1")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert release.validate_tag(tmp_path, "v0.1.0a1") == "0.1.0a1"
    release.validate_tracked_paths(tmp_path)
    release.validate_clean_checkout(tmp_path)
    (tmp_path / "untracked.txt").write_text("private placeholder")
    with pytest.raises(ValueError, match="checkout") as dirty:
        release.validate_clean_checkout(tmp_path)
    assert "private placeholder" not in str(dirty.value)
    (tmp_path / "untracked.txt").unlink()
    with pytest.raises(ValueError, match="exactly match"):
        release.validate_tag(tmp_path, "v0.2.0")
    (tmp_path / ".env").write_text("test-placeholder")
    git("add", ".env")
    with pytest.raises(ValueError, match="tracked") as error:
        release.validate_tracked_paths(tmp_path)
    assert "test-placeholder" not in str(error.value)


def test_reproducibility_checker_rejects_missing_or_changed_artifacts(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        (directory / ".gitignore").write_text("*\n")
        (directory / "file.whl").write_bytes(b"wheel")
        (directory / "file.tar.gz").write_bytes(b"source")
    repro.compare(first, second)
    (second / "file.whl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differ"):
        repro.compare(first, second)
    (second / "file.whl").unlink()
    with pytest.raises(ValueError, match="same"):
        repro.compare(first, second)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        ".envrc",
        "private/orders.sqlite-journal",
        "private/cache.db-wal",
        "subfolder/key.pem",
    ],
)
def test_release_flags_sensitive_paths(path):
    assert release.sensitive_path(path)


def test_release_allows_documented_environment_samples():
    assert not release.sensitive_path(".env.sample")
    assert not release.sensitive_path(".env.example")
