"""Distribution contains the application, not local media, credentials or development history."""

import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tarfile

import pytest

SOURCE = Path(__file__).resolve().parents[1]
packaging = runpy.run_path(str(SOURCE / "scripts/package_source.py"))


@pytest.fixture
def source(tmp_path):
    for relative in packaging["source_files"](SOURCE):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
    return tmp_path


def test_source_archive_is_reproducible_and_contains_only_public_inventory(source, tmp_path):
    (source / "workspace").mkdir()
    (source / "workspace/private.json").write_text("private")
    (source / "prototype").mkdir()
    (source / "prototype/unused.py").write_text("unused")
    (source / "artifacts").mkdir()
    (source / "artifacts/history.md").write_text("history")
    first = packaging["package"](source, tmp_path / "first")
    second = packaging["package"](source, tmp_path / "second")
    assert first["sha256"] == second["sha256"]
    assert hashlib.sha256(Path(first["archive"]).read_bytes()).hexdigest() == first["sha256"]
    with tarfile.open(first["archive"]) as archive:
        names = archive.getnames()
        assert not any(
            part in n.split("/")
            for n in names
            for part in ("workspace", "prototype", "artifacts", "__pycache__", "models", "node_modules")
        )
        manifest = json.load(archive.extractfile(next(n for n in names if n.endswith("/manifest.json"))))
        for filename, checksum in manifest["files"].items():
            content = archive.extractfile(f"ownsei-{first['version']}-source/{filename}").read()
            assert hashlib.sha256(content).hexdigest() == checksum
    with pytest.raises(ValueError, match="already exists"):
        packaging["package"](source, tmp_path / "first")


@pytest.mark.parametrize(
    "bad", ["-----BEGIN " + "PRIVATE KEY-----", "ghp_" + "x" * 36, "/" + "home/user/private"]
)
def test_audit_rejects_private_data_before_creating_output(source, tmp_path, bad):
    (source / "README.md").write_text(bad)
    output = tmp_path / "release"
    with pytest.raises(ValueError):
        packaging["package"](source, output)
    assert not output.exists()


def test_audit_rejects_missing_files_and_symlinks(source):
    readme = source / "README.md"
    readme.unlink()
    assert {"file": "README.md", "reason": "missing required file"} in packaging["audit"](
        source, packaging["source_files"](source)
    )
    readme.symlink_to(source / "LICENSE")
    assert {"file": "README.md", "reason": "symlink"} in packaging["audit"](
        source, packaging["source_files"](source)
    )
