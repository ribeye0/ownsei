"""On-demand release discovery and background package download for About."""

from importlib.metadata import version
import hashlib
import os
import re
import threading

import httpx

from filelock import Timeout
from onsei.update_install import install_gate, read, target_of, update_dir, version_key
from onsei.util import ROOT, atomic_json

API = "https://api.github.com/repos/ribeye0/ownsei/releases"
RESTART_EXIT = 75


def headers(binary=False):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    result = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json"}
    if token:
        result["Authorization"] = "Bearer " + token
    return result


def release_offer(client, current, target):
    response = client.get(API + "/latest", headers=headers())
    if response.status_code == 404:
        raise ValueError("找不到可讀取的正式發布。私人儲存庫需設定具有讀取權限的 GH_TOKEN。")
    response.raise_for_status()
    release = response.json()
    latest = release["tag_name"].removeprefix("v")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("找不到正式版本")
    if version_key(latest) <= version_key(current) or target is None:
        return {"version": latest}
    suffix = ".zip" if target.startswith("windows-") else ".tar.gz"
    name = f"ownsei-{latest}-{target}{suffix}"
    assets = {a["name"]: a for a in release["assets"] if a.get("state") == "uploaded"}
    if name not in assets:
        raise ValueError("此版本尚未提供對應平台的更新包")
    item = assets[name]
    digest = (item.get("digest") or "").removeprefix("sha256:")
    if not re.fullmatch("[0-9a-f]{64}", digest) and "SHA256SUMS" in assets:
        checksum = client.get(asset_url(assets["SHA256SUMS"]["id"]), headers=headers(True))
        checksum.raise_for_status()
        digest = next(
            (
                line.split()[0]
                for line in checksum.text.splitlines()
                if len(line.split()) == 2 and line.split()[1] == name
            ),
            "",
        )
    if not re.fullmatch("[0-9a-f]{64}", digest) or item["size"] <= 0:
        raise ValueError("發布包缺少 SHA-256 或檔案大小")
    return {
        "version": latest,
        "target": target,
        "id": int(item["id"]),
        "total": item["size"],
        "sha256": digest,
    }


def asset_url(identity):
    return f"{API}/assets/{int(identity)}"


def error_text(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "無法連線至 GitHub，請檢查網路後重試"
    return str(exc)


class Updater:
    def __init__(self, root):
        self.root = root
        self.cache = update_dir(root)
        self.lock = threading.RLock()
        release = root / "release.json"
        meta = read(release) if release.is_file() else {}
        self.current = meta.get("version") or version("ownsei")
        self.target = target_of(meta) if meta else None
        self.managed = bool(self.target and os.environ.get("OWNSEI_UPDATE_LAUNCHER"))
        self.offer = {}
        self.state = {
            "phase": "idle",
            "current": self.current,
            "managed": self.managed,
            "version": "",
            "bytes": 0,
            "total": 0,
            "error": "",
        }
        saved = self.saved_offer()
        if saved:
            self.offer = saved
            self.state.update(
                phase="ready", version=saved["version"], total=saved["total"], bytes=saved["total"]
            )
        if self.managed and (self.cache / "error.txt").is_file():
            self.state.update(error=(self.cache / "error.txt").read_text(encoding="utf-8"))

    def saved_offer(self):
        if self.managed:
            try:
                offer = read(self.cache / "ready.json")
                if (
                    offer["target"] == self.target
                    and version_key(offer["version"]) > version_key(self.current)
                    and (self.cache / "package").stat().st_size == offer["total"]
                ):
                    return offer
            except (ValueError, KeyError, TypeError, OSError):
                pass
        return None

    def status(self):
        with self.lock:
            return self.state.copy()

    def begin(self, action):
        with self.lock:
            phase = self.state["phase"]
            if phase in ("checking", "downloading", "installing"):
                return self.status()
            if action == "download" and (not self.managed or not self.offer.get("id")):
                raise ValueError("請先透過發布版啟動器檢查更新")
            if action == "download" and phase == "ready":
                return self.status()
            self.state.update(phase="checking" if action == "check" else "downloading", error="", bytes=0)
            threading.Thread(target=self.worker, args=(action,), daemon=True).start()
            return self.status()

    def worker(self, action):
        try:
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, connect=10)) as client:
                if action == "check":
                    offer = release_offer(client, self.current, self.target)
                    phase = (
                        "available"
                        if version_key(offer["version"]) > version_key(self.current)
                        else "current"
                    )
                    if phase == "available" and self.saved_offer() == offer:
                        phase = "ready"
                    with self.lock:
                        self.offer = offer
                        self.state.update(
                            phase=phase,
                            version=offer["version"],
                            total=offer.get("total", 0),
                            bytes=offer.get("total", 0) if phase == "ready" else 0,
                        )
                    return
                self.download(client)
                with self.lock:
                    self.state.update(phase="ready")
        except Exception as exc:
            with self.lock:
                saved = self.saved_offer()
                if saved:
                    self.offer = saved
                    self.state.update(
                        phase="ready",
                        version=saved["version"],
                        bytes=saved["total"],
                        total=saved["total"],
                        error=error_text(exc),
                    )
                else:
                    self.state.update(phase="error", error=error_text(exc))

    def download(self, client):
        offer = self.offer
        self.cache.mkdir(parents=True, exist_ok=True)
        partial = self.cache / "package.part"
        digest = hashlib.sha256()
        count = 0
        try:
            with client.stream("GET", asset_url(offer["id"]), headers=headers(True)) as reply:
                reply.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in reply.iter_bytes(128 * 1024):
                        count += len(chunk)
                        if count > offer["total"]:
                            raise ValueError("更新包大小不符")
                        output.write(chunk)
                        digest.update(chunk)
                        with self.lock:
                            self.state["bytes"] = count
            if count != offer["total"] or digest.hexdigest() != offer["sha256"]:
                raise ValueError("更新包不完整或 SHA-256 不符，請重新下載")
            partial.replace(self.cache / "package")
            atomic_json(self.cache / "ready.json", offer)
            (self.cache / "error.txt").unlink(missing_ok=True)
        finally:
            partial.unlink(missing_ok=True)

    def install(self):
        with self.lock:
            if not self.managed or self.state["phase"] != "ready":
                raise ValueError("尚無可安裝的更新")
            try:
                with install_gate(self.root).write_lock():
                    self.state.update(phase="installing", error="")
            except Timeout as error:
                raise ValueError("請先關閉同一安裝目錄的其他 Ownsei 視窗") from error
            return self.status()


updates = Updater(ROOT)
