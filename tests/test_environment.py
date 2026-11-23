"""Vulkan deployment does not depend on NVIDIA tools or CUDA libraries."""

from onsei import environment as env


def test_readiness_and_prerequisite_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "ROOT", tmp_path)
    monkeypatch.setattr(env.platform, "system", lambda: "Linux")
    monkeypatch.setattr(env.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(env.platform, "libc_ver", lambda: ("glibc", "2.39"))
    monkeypatch.setattr(env.shutil, "which", lambda name: "/bin/" + name if name != "nvidia-smi" else None)
    monkeypatch.setattr(env, "command", lambda *a, **k: (True, "available"))
    monkeypatch.setattr(
        env.backend,
        "inventory",
        lambda: {
            e: {"devices": [{"id": "Vulkan0", "name": "AMD"}], "error": None} for e in ("speech", "llama")
        },
    )
    assert env.inspect_environment(engines=False)["ready"]
    assert not env.inspect_environment()["ready"]
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").touch()
    assert env.inspect_environment()["ready"]
    monkeypatch.setattr(env.platform, "libc_ver", lambda: ("glibc", "2.35"))
    assert not env.inspect_environment(engines=False)["ready"]


def test_missing_engines_fail_but_cpu_only_is_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "ROOT", tmp_path)
    monkeypatch.setattr(env.shutil, "which", lambda _name: None)
    assert not env.inspect_environment(engines=False)["ready"]
    monkeypatch.setattr(env.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(env, "command", lambda *a, **k: (True, "available"))
    monkeypatch.setattr(env.platform, "libc_ver", lambda: ("glibc", "2.44"))
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").touch()
    monkeypatch.setattr(
        env.backend,
        "inventory",
        lambda: {e: {"devices": [{"id": "CPU", "name": "CPU"}], "error": None} for e in ("speech", "llama")},
    )
    report = env.inspect_environment()
    assert report["ready"]
    assert not any(c["name"] == "nvidia" for c in report["checks"])
    monkeypatch.setattr(
        env.backend,
        "inventory",
        lambda: {e: {"devices": [], "error": "engine missing"} for e in ("speech", "llama")},
    )
    assert not env.inspect_environment()["ready"]
