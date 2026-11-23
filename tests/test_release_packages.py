"""Release contents, platform/backend separation and public-file manifest."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def packaging(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("package_release")


@pytest.mark.parametrize(
    "target", ["linux-x64-vulkan", "linux-x64-cuda", "windows-x64-vulkan", "windows-x64-cuda"]
)
def test_platform_package_contains_ui_engines_and_matching_manifest(tmp_path, monkeypatch, packaging, target):
    source = tmp_path / "source"
    source.mkdir()
    for relative in packaging.source_files(ROOT):
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
        if target.startswith("windows"):
            # Reproducible source archives use epoch timestamps, before ZIP's
            # supported 1980 boundary. Rebuilding those sources must still work.
            os.utime(destination, (0, 0))
    (source / "frontend/dist").mkdir()
    (source / "frontend/dist/index.html").write_text("<title>Ownsei</title>")
    (source / "models").mkdir()
    (source / "models/private.gguf").write_bytes(b"private")
    calls = []

    def install(root, engine, assets, platform, cache):
        calls.append((engine, assets))
        directory = root / "bin" / engine
        directory.mkdir(parents=True)
        executable = ("speech" if engine == "speech" else "llama-server") + (
            ".exe" if platform == "windows" else ""
        )
        (directory / executable).write_bytes(b"engine")
        (directory / "ownsei-engine.json").write_text("{}")
        if engine == "llama":
            (directory / "unused-converter").write_bytes(b"extra")
            library = "llama-server-impl.dll" if platform == "windows" else "libllama-server-impl.so"
            (directory / library).write_bytes(b"server implementation")

    monkeypatch.setattr(packaging, "install_engine", install)
    monkeypatch.setattr(packaging.subprocess, "check_output", lambda *a, **k: "a" * 40)
    output = tmp_path / "output"
    result = packaging.build_release(source, output, target, tmp_path / "cache", tmp_path)
    archive = output / result["file"]
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert "vulkan" in calls[0][1][0]["name"]  # Haas always Vulkan, even in CUDA bundles.
    assert ("cuda" in calls[1][1][0]["name"]) == target.endswith("cuda")
    if target.startswith("windows"):
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            read = z.read
            manifest = json.loads(read(next(n for n in names if n.endswith("/manifest.json"))))
            prefix = next(n for n in names if n.endswith("/manifest.json")).removesuffix("manifest.json")
            for name, sha in manifest["files"].items():
                assert hashlib.sha256(read(prefix + name)).hexdigest() == sha
    else:
        with tarfile.open(archive) as z:
            names = z.getnames()
            manifest_name = next(n for n in names if n.endswith("/manifest.json"))
            manifest = json.load(z.extractfile(manifest_name))
            prefix = manifest_name.removesuffix("manifest.json")
            for name, sha in manifest["files"].items():
                assert hashlib.sha256(z.extractfile(prefix + name).read()).hexdigest() == sha
    assert any("llama-server-impl" in n for n in names)
    assert any(n.endswith("/frontend/dist/index.html") for n in names)
    assert not any(
        "/tests/" in n or "/models/" in n or "/frontend/src/" in n or "unused-converter" in n for n in names
    )
    assert any("NVIDIA-CUDA-EULA" in n for n in names) == target.endswith("cuda")
    contents = set(manifest["files"])
    assert "scripts/launch.py" in contents
    if target.startswith("linux"):
        assert "ownsei.sh" in contents
        assert "scripts/install.sh" in contents
        assert "runtimes/vad.lock" in contents
        assert not any(n.endswith((".cmd", ".bat", ".ps1")) or "-windows." in n for n in contents)
    else:
        assert {"Ownsei.cmd", "Install.cmd", "scripts/start.ps1", "scripts/choose_path.ps1"} <= contents
        assert "runtimes/vad-windows.lock" in contents
        assert "runtimes/vad.lock" not in contents
        assert not any(n.endswith(".sh") for n in contents)
