"""Fresh installation checks fail early and optional runtimes stay optional."""

from onsei import environment as env


def test_readiness_and_prerequisite_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "ROOT", tmp_path)
    monkeypatch.setattr(env.platform, "system", lambda: "Linux")
    monkeypatch.setattr(env.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(env.platform, "libc_ver", lambda: ("glibc", "2.39"))
    monkeypatch.setattr(env.shutil, "which", lambda name: "/bin/" + name)

    def run(args, **_kwargs):
        if "nvidia-smi" in args[0]:
            return True, "Test GPU, 570.124.06, 12288"
        if "--list-devices" in args:
            return True, "Available devices:\n CUDA0: Test GPU"
        return True, "available"

    monkeypatch.setattr(env, "command", run)
    monkeypatch.setattr(env, "llama_binary", lambda: tmp_path / "llama-server")
    assert env.inspect_environment(engines=False)["ready"]
    assert not env.inspect_environment()["ready"]
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").touch()
    assert env.inspect_environment()["ready"]
    monkeypatch.setattr(env.platform, "libc_ver", lambda: ("glibc", "2.35"))
    assert not env.inspect_environment(engines=False)["ready"]


def test_missing_gpu_or_incompatible_driver_cannot_appear_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "ROOT", tmp_path)
    monkeypatch.setattr(env.shutil, "which", lambda _name: None)
    report = env.inspect_environment(engines=False)
    assert not report["ready"] and not report["gpus"]
    monkeypatch.setattr(env.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(env, "command", lambda *_args, **_kwargs: (True, "Older GPU, 535.10.1, 8000"))
    check = next(c for c in env.inspect_environment(engines=False)["checks"] if c["name"] == "nvidia")
    assert not check["ok"]
