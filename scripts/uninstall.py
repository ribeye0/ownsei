"""Plan an offline release uninstall. Never recurse over a model library or installation root."""

import argparse
from contextlib import closing
import errno
import json
from pathlib import Path, PureWindowsPath
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from onsei.catalog import REGISTRY  # noqa: E402
from onsei.project_paths import project_location  # noqa: E402
from onsei.user_paths import config_dir, data_dir, download_cache, library_setting  # noqa: E402

# These files are needed to preview or retry an interrupted removal.
BOOTSTRAP = {
    "manifest.json",
    "release.json",
    "uninstall.sh",
    "Uninstall.cmd",
    "scripts/uninstall.py",
    "scripts/uninstall.ps1",
    "src/onsei/__init__.py",
    "src/onsei/catalog.py",
    "src/onsei/project_paths.py",
    "src/onsei/user_paths.py",
}


def linked(path):
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_relative(name):
    windows = PureWindowsPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or windows.drive
        or windows.root
        or any(part in {"", ".", ".."} or part.rstrip(" .") != part for part in name.split("/"))
    ):
        raise ValueError(f"無效的檔案清單路徑：{name!r}")
    return Path(name)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def db_rows(path, sql):
    # Without a WAL, immutable reads avoid creating SQLite sidecar files during preview.
    flags = "?mode=ro" if path.with_name(path.name + "-wal").exists() else "?mode=ro&immutable=1"
    with closing(sqlite3.connect(path.as_uri() + flags, uri=True)) as db:
        return db.execute(sql).fetchall()


def protected_paths(root):
    workspace = root / "workspace"
    protected = {workspace, workspace.resolve()}
    stems = {}
    index = workspace / "index.db"
    if index.is_file():
        for identity, saved in db_rows(index, "SELECT id,path FROM projects"):
            try:
                project = project_location(workspace, identity, saved)
            except ValueError:
                if PureWindowsPath(saved).is_absolute() and sys.platform != "win32":
                    continue  # An external project registered on another OS.
                raise
            protected.update((project, project.resolve()))
            db = project / "project.db"
            if db.is_file():
                for (payload,) in db_rows(db, "SELECT payload FROM tracks"):
                    source = Path(json.loads(payload)["source"])
                    if source.is_absolute():
                        # Also retain subtitles exported next to the audio.
                        protected.add(source)
                        protected.add(source.resolve())
                        protected.add(source.parent / "sub")
                        stems.setdefault(source.parent, set()).add(source.stem)
    for directory, names in stems.items():
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.suffix.lower() in {".vtt", ".srt", ".lrc", ".ass", ".txt", ".jsonl"} and (
                    entry.stem in names or Path(entry.stem).stem in names
                ):
                    protected.update((entry, entry.resolve()))
    return protected


def legacy_model_names(library):
    return [p.name for p in library.iterdir() if p.name.startswith("whisper-")]


class Plan:
    def __init__(self, root):
        self.protected = protected_paths(root)
        self.files = set()
        self.dirs = set()
        self.last = set()

    def allowed(self, path):
        return not any(p in self.protected for p in (path, *path.parents)) and not any(
            linked(p) for p in path.parents
        )

    def empty(self, path):
        if self.allowed(path) and not linked(path) and path.is_dir() and not path.is_mount():
            self.dirs.add(path)

    def file(self, path, *, last=False):
        if self.allowed(path) and (linked(path) or path.is_file()):
            (self.last if last else self.files).add(path)

    def tree(self, path):
        if not self.allowed(path):
            return
        if linked(path) or not path.is_dir():
            self.file(path)
        elif not path.is_mount():
            self.dirs.add(path)
            for child in path.iterdir():
                self.tree(child)

    def library(self, path):
        if not self.allowed(path) or linked(path) or not path.is_dir():
            return
        for name in {*REGISTRY, "firered-vad-ncnn", *legacy_model_names(path)}:
            directory = path / name
            if linked(directory) or not directory.is_dir() or not self.allowed(directory):
                continue
            receipt = directory / "onsei-model.json"
            if linked(receipt) or not receipt.is_file():
                # Old interrupted downloads may only have HF transfer metadata.
                self.tree(directory / ".cache" / "huggingface")
                self.empty(directory / ".cache")
                self.empty(directory)
                continue
            meta = read_json(receipt)
            hashes = meta.get("sha256")
            if not isinstance(hashes, dict) or not hashes or not meta.get("repo"):
                raise ValueError(f"模型紀錄無效：{receipt}")
            if not all(
                isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)
                for h in hashes.values()
            ):
                raise ValueError(f"模型紀錄的雜湊無效：{receipt}")
            for name in hashes:
                # Model manifests use flat filenames; include older model revisions.
                relative = safe_relative(name)
                if len(relative.parts) != 1:
                    raise ValueError(f"模型紀錄含子路徑：{receipt}")
                self.file(directory / relative)
            self.file(receipt, last=True)
            if directory.name == "firered-vad":
                self.empty(directory / "VAD")
            self.tree(directory / ".cache" / "huggingface")
            self.empty(directory / ".cache")
            self.empty(directory)
        self.tree(path / ".cache" / "xet")
        self.empty(path / ".cache")

    def ordered(self):
        return {
            "files": [str(p) for p in sorted(self.files - self.last)],
            "last": [str(p) for p in sorted(self.last)],
            "directories": [str(p) for p in sorted(self.dirs, key=lambda p: (-len(p.parts), str(p)))],
        }


def make_plan(root, extra):
    manifest = root / "manifest.json"
    if not manifest.is_file() or linked(manifest) or (root / ".git").exists():
        raise ValueError("請從解壓後的 Ownsei 平台發布包執行解除安裝。")
    files = read_json(manifest)["files"]
    if not {"release.json", "src/onsei/cli.py", "pyproject.toml"} <= files.keys():
        raise ValueError("無法辨識 Ownsei 發布清單。")
    plan = Plan(root)
    for name in [*files, "manifest.json"]:
        relative = safe_relative(name)
        path = root / relative
        plan.file(path, last=name in BOOTSTRAP)
        for parent in relative.parents:
            plan.empty(root / parent)
    for child in (".tools", ".venv", ".runtime-envs"):
        plan.tree(root / child)
    for child in {str(Path(name).parent) for name in files if name.endswith(".py")}:
        cache = root / child / "__pycache__"
        if plan.allowed(cache) and not linked(cache) and cache.is_dir():
            for pyc in cache.glob("*.pyc"):
                if (Path(child) / (pyc.name.split(".")[0] + ".py")).as_posix() in files:
                    plan.file(pyc)
            plan.empty(cache)
    plan.empty(root / "runtimes")
    config, data, setting = config_dir(), data_dir(), library_setting()
    libraries = {root / "models", config / "models", data / "models"}
    standard = libraries.copy()
    if plan.allowed(setting) and not linked(setting) and setting.is_file():
        extra = [*extra, read_json(setting)["models_dir"]]
    caches = {download_cache(data / "models")}
    for location in extra:
        path = Path(location).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"模型庫需要絕對路徑：{location}")
        libraries.add(path)
        caches.add(download_cache(path))
    for path in sorted(libraries):
        if path.exists() or path not in standard:
            print(f"模型庫    {path}" + ("" if path.is_dir() else "  （無法存取，本次略過）"))
        plan.library(path)
        if path in standard:
            plan.empty(path)
    for cache in sorted(caches):
        print(f"下載快取  {cache}")
        for child in ("hub", "xet"):
            plan.tree(cache / child)
        for model in REGISTRY:
            plan.tree(cache / "downloads" / model)
        plan.empty(cache / "downloads")
        plan.empty(cache)
    if sys.platform != "win32":
        uv_receipt = config.parent / "uv/uv-receipt.json"
        if plan.allowed(uv_receipt) and not linked(uv_receipt) and uv_receipt.is_file():
            try:
                owner = read_json(uv_receipt).get("install_prefix")
            except (ValueError, AttributeError):
                owner = None
            if owner == str(root / ".tools"):
                plan.file(uv_receipt)
                plan.empty(uv_receipt.parent)
    for name in ("interface.json", "global-preferences.json"):
        plan.file(config / name)
        plan.file(config / (name + ".lock"))
    for name in ("gpu.lock", "model-operation.lock", "library-change.lock"):
        plan.file(config / ".locks" / name)
    plan.empty(config / ".locks")
    plan.file(setting, last=True)
    plan.empty(config)
    plan.empty(data)
    # Windows installs FFmpeg separately; engine repair can also leave archive parts.
    release = read_json(root / "release.json")
    archives = ["ffmpeg-windows-lgpl.zip"]
    for assets in release["engines"].values():
        archives.extend(asset["name"] for asset in assets)
    for archive in archives:
        relative = safe_relative(archive)
        if len(relative.parts) != 1:
            raise ValueError("無效的引擎檔名")
        for suffix in ("", ".assembling", ".parts", ".parts-8m"):
            plan.tree(root / "downloads" / (archive + suffix))
    plan.empty(root / "downloads")
    ffmpeg = root / "bin/ffmpeg"
    if plan.allowed(ffmpeg) and not linked(ffmpeg) and ffmpeg.is_dir():
        for path in ffmpeg.iterdir():
            if path.name in {"ffmpeg.exe", "ffprobe.exe"} or path.suffix.lower() == ".dll":
                plan.file(path)
        plan.tree(ffmpeg / "licenses")
        plan.empty(ffmpeg)
    return plan


def ensure_idle():
    try:
        import psutil
    except ImportError:
        return  # An unpacked release may be using system Python before first install.
    user = psutil.Process().username()
    for proc in psutil.process_iter(["cmdline", "username"]):
        args = proc.info["cmdline"] or []
        if proc.info["username"] != user:
            continue
        if "serve" in args and any(Path(a).name.lower() in {"ownsei", "ownsei.exe"} for a in args):
            raise ValueError(f"請先關閉 Ownsei 後端（PID {proc.pid}）。")
        if "-m" in args:
            module = args[args.index("-m") + 1 :][:1]
            if module and module[0] in {
                "onsei.cli",
                "onsei.runner",
                "onsei.modelmanager",
                "onsei.importjobs",
            }:
                raise ValueError(f"請先關閉 Ownsei 後端與任務（PID {proc.pid}），再重新執行。")


def remove(plan):
    for key in ("files", "last", "directories"):
        for name in plan[key]:
            path = Path(name)
            if any(linked(p) for p in path.parents):
                raise ValueError(f"路徑已變更：{path}")
            if key != "directories":
                path.unlink(missing_ok=True)
            else:
                try:
                    path.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise


def main():
    parser = argparse.ArgumentParser(description="移除 Ownsei 發布版，保留 workspace。")
    parser.add_argument("--dry-run", action="store_true", help="顯示待刪檔案")
    parser.add_argument("--yes", action="store_true", help="略過互動確認")
    parser.add_argument("--models-dir", action="append", default=[], help="額外清理曾使用的模型庫，可重複")
    parser.add_argument("--windows-plan", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    print(f"Ownsei 解除安裝\n程式      {ROOT}\n保留      {ROOT / 'workspace'}\n全局設定  {config_dir()}")
    plan = make_plan(ROOT, args.models_dir).ordered()
    print(
        f"待移除 {len(plan['files']) + len(plan['last'])} 個檔案。全局設定與模型也供同一帳號的其他 Ownsei 安裝使用。"
    )
    if args.dry_run:
        for key in ("files", "last"):
            for name in plan[key]:
                print(name)
        return
    ensure_idle()
    if not args.yes and input("確定移除？輸入 DELETE 繼續，其餘取消：").strip() != "DELETE":
        print("已取消。")
        return
    ensure_idle()
    if args.windows_plan:
        args.windows_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    elif sys.platform == "win32":
        raise ValueError("請使用 Uninstall.cmd，以便退出 Python 後移除執行環境。")
    else:
        remove(plan)
        print("Ownsei 移除完成。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, EOFError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        sys.exit(f"未完成移除：{exc}")
