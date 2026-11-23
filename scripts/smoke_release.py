"""Exercise an installed release over real HTTP using an isolated data directory.

Uses synthetic audio and fixture subtitles. This is not ASR/LLM or native-dialog
acceptance. Run with the release's own Python; no test dependencies are needed.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
import zipfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Smoke:
    def __init__(self, root, output):
        self.root, self.output = root, output
        output.mkdir(parents=True, exist_ok=False)
        self.sandbox = output / "app"
        self.sandbox.mkdir()
        self.env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "ONSEI_ROOT": str(self.sandbox),
            "PYTHONPATH": str(root / "src"),
            "PATH": os.pathsep.join(
                [str(root / "bin/ffmpeg/bin"), str(root / "bin/ffmpeg"), os.environ.get("PATH", "")]
            ),
        }
        self.report = {
            "status": "running",
            "platform": platform.platform(),
            "python": sys.version,
            "release_root": str(root),
            "scope": "Real HTTP, synthetic audio and fixture subtitles; no GPU inference or native dialog.",
            "steps": [],
        }
        self.server = None
        self.server_log = None
        self.save()

    def save(self):
        temporary = self.output / "report.json.pending"
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.output / "report.json")

    @contextmanager
    def step(self, name):
        row = {"name": name, "status": "running"}
        self.report["steps"].append(row)
        self.save()
        started = time.monotonic()
        try:
            yield
            row["status"] = "passed"
        except Exception as error:
            row.update(status="failed", error=str(error))
            raise
        finally:
            row["seconds"] = round(time.monotonic() - started, 3)
            self.save()

    def request(self, path, method="GET", data=None, status=200, headers=None, raw=False):
        request = urllib.request.Request(
            self.url + path,
            method=method,
            data=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
            require(
                response.status == status,
                f"{method} {path}: expected {status}, got {response.status}: {content[:1000]!r}",
            )
            return content if raw else json.loads(content)

    def start(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server_log = (self.output / "server.log").open("ab")
        self.server = subprocess.Popen(
            [sys.executable, "-m", "onsei.cli", "serve", "--port", str(port)],
            cwd=self.root,
            env=self.env,
            stdout=self.server_log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(120):
            require(self.server.poll() is None, "Server exited; inspect server.log")
            try:
                page = self.request("/", raw=True)
                require(b'<div id="root"' in page, "Built frontend was not served")
                return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        raise RuntimeError("Server readiness timeout; inspect server.log")

    def stop(self):
        if self.server is not None and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        if self.server_log is not None:
            self.server_log.close()
        self.server = self.server_log = None

    def run(self):
        with self.step("release-integrity"):
            metadata = json.loads((self.root / "release.json").read_text())
            self.report["release"] = metadata
            require(
                metadata["platform"].lower() == platform.system().lower(),
                "Use a release for the current native OS",
            )
            require(
                Path(sys.prefix).resolve() == (self.root / ".venv").resolve(),
                "Run with the release's .venv Python",
            )
            files = json.loads((self.root / "manifest.json").read_text())["files"]
            for name, expected in files.items():
                path = (self.root / name).resolve()
                require(path.is_relative_to(self.root), f"Unsafe manifest path: {name}")
                require(file_hash(path) == expected, f"SHA256 mismatch: {name}")
            if metadata["platform"] == "windows":
                require(
                    not any(name.lower().endswith(".sh") for name in files),
                    "Windows manifest contains Linux scripts",
                )
                require(
                    not list(self.root.glob("*.sh")) and not list((self.root / "scripts").rglob("*.sh")),
                    "Stale Linux scripts in Windows installation",
                )
            self.report["manifest_files_checked"] = len(files)
            shutil.copytree(self.root / "frontend/dist", self.sandbox / "frontend/dist")

        with self.step("synthetic-audio-fixtures"):
            audio = self.sandbox / "audio 日本語 & [test]"
            audio.mkdir()
            wav = audio / "01 台詞 #%.wav"
            with wave.open(str(wav), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0\0" * 32000)
            second = audio / "02 テスト.wav"
            with wave.open(str(second), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0\0" * 48000)
            mp3 = wav.with_suffix(".mp3")
            with (self.output / "ffmpeg.log").open("wb") as log:
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-y", "-i", str(wav), str(mp3)],
                    env=self.env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=30,
                )
            originals = {p: file_hash(p) for p in [wav, second, mp3]}

        with self.step("http-start-and-project-create"):
            self.start()
            project = self.request("/api/projects", "POST", {"title": "Windows 驗收 專案"})["id"]
            base = f"/api/projects/{project}"
            details = self.request(base)
            project_path = Path(details["path"])
            require(project_path.is_relative_to(self.sandbox), "Project escaped the isolated sandbox")
            require(len(self.request("/api/projects")) == 1, "Unexpected existing projects in sandbox")

        with self.step("async-import-progress-and-format-deduplication"):
            job = self.request(base + "/imports", "POST", {"path": str(audio)})
            states = []
            for _ in range(240):
                state = self.request(base + "/imports/" + job["id"])
                states.append(state)
                if state["status"] != "running":
                    break
                time.sleep(0.25)
            (self.output / "import-progress.json").write_text(
                json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            require(state["status"] == "completed", f"Import failed or timed out: {state}")
            tracks = self.request(base)["tracks"]
            require(
                len(tracks) == 2 and all(Path(t["source"]).suffix == ".wav" for t in tracks),
                "WAV/MP3 deduplication failed",
            )

        with self.step("http-audio-seek-ranges"):
            for track in tracks:
                expected = Path(track["source"]).read_bytes()
                path = base + f"/tracks/{track['id']}/audio"
                self.request(path, method="HEAD", raw=True)
                for start in [0, len(expected) // 2, len(expected) - 64]:
                    actual = self.request(
                        path, headers={"Range": f"bytes={start}-{start + 31}"}, status=206, raw=True
                    )
                    require(
                        actual == expected[start : start + 32], "Audio range response does not match source"
                    )

        with self.step("seed-subtitle-fixtures-not-model-output"):
            self.stop()
            script = (
                "from onsei.storage import Store\nfrom onsei.domain import Cue\nimport sys\n"
                "s=Store(sys.argv[1])\n"
                "for t in s.tracks():\n"
                " for v in ['fixture-v1','fixture-v2']:\n"
                "  s.replace_cues(t,v,[Cue(id=t.id+v,track_id=t.id,version=v,ordinal=0,start_ms=100,end_ms=1200,"
                "ja_raw='こんにちは',zh_auto='你好',translation_status='translated')])\n"
            )
            subprocess.run(
                [sys.executable, "-c", script, str(project_path)],
                cwd=self.root,
                env=self.env,
                check=True,
                timeout=30,
            )
            self.start()

        with self.step("version-switch-edit-and-conflict"):
            for track in tracks:
                path = base + f"/tracks/{track['id']}"
                for _ in range(5):
                    for version in ["fixture-v1", "fixture-v2"]:
                        self.request(path + "/versions/" + version, "POST")
                        require(len(self.request(path + "/versions")) == 2, "Version list became unavailable")
                cue = self.request(path + "/cues")[0]
                edit = {"revision": cue["revision"], "changes": {"zh_override": "你好！"}}
                edited = self.request(base + "/cues/" + cue["id"], "PATCH", edit)
                require(edited["zh"] == "你好！", "Manual edit lost")
                self.request(base + "/cues/" + cue["id"], "PATCH", edit, status=409)

        with self.step("export-formats-names-zip-and-sub-directory"):
            selection = []
            for track in tracks:
                results = self.request(base + f"/tracks/{track['id']}/translations")
                selected = next(row["id"] for row in results if row["active"])
                selection.append({"track_id": track["id"], "version": "fixture-v2", "result_id": selected})
            result = self.request(
                base + "/deliveries",
                "POST",
                {
                    "selections": selection,
                    "languages": ["zh"],
                    "formats": ["vtt", "srt", "lrc", "ass"],
                    "destination": "sub",
                },
            )
            (self.output / "delivery.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            require(
                len(result["files"]) == 8 and result["has_zip"], "Incomplete exports; inspect delivery.json"
            )
            export_dir = self.output / "downloads"
            export_dir.mkdir()
            for row in result["downloads"]:
                content = self.request(base + f"/deliveries/{result['id']}/files/{row['index']}", raw=True)
                require(
                    "你好！" in content.decode("utf-8-sig"), "Wrong subtitle language or missing edited text"
                )
                (export_dir / row["name"]).write_bytes(content)
            archive = self.request(base + "/deliveries/" + result["id"], raw=True)
            zip_path = export_dir / "subtitles.zip"
            zip_path.write_bytes(archive)
            with zipfile.ZipFile(zip_path) as bundle:
                require(bundle.testzip() is None, "Corrupt ZIP")
            for track in tracks:
                source = Path(track["source"])
                for suffix in [".vtt", ".srt", ".lrc", ".ass"]:
                    require(
                        (source.parent / "sub" / source.with_suffix(suffix).name).is_file(),
                        "Filename not preserved in sub directory",
                    )

        with self.step("restart-persistence-and-project-trash-restore-purge"):
            self.stop()
            self.start()
            require(len(self.request(base)["tracks"]) == 2, "Tracks not retained after restart")
            for track in tracks:
                require(
                    self.request(base + f"/tracks/{track['id']}/cues")[0]["zh"] == "你好！",
                    "Edit not retained after restart",
                )
            self.request(base, "DELETE")
            self.request(base, status=410)
            self.request(base + "/restore", "POST")
            self.request(base, "DELETE")
            result = self.request(base + "/purge", "DELETE")
            require(result["scope"] == "workspace" and not project_path.exists(), "Permanent deletion failed")
            require(not self.request("/api/projects?trashed=true"), "Trash still contains project")

        with self.step("source-audio-unchanged-and-database-integrity"):
            self.stop()
            require(
                all(file_hash(path) == expected for path, expected in originals.items()),
                "Source audio changed",
            )
            with closing(sqlite3.connect(self.sandbox / "workspace/index.db")) as db:
                require(
                    db.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                    "Registry integrity check failed",
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="A new directory; retained for inspection")
    args = parser.parse_args()
    smoke = Smoke(args.release_root.resolve(), args.output.resolve())
    try:
        smoke.run()
        smoke.report["status"] = "passed"
    except Exception as error:
        smoke.report.update(status="failed", error=str(error))
    finally:
        smoke.stop()
        smoke.save()
    print(json.dumps(smoke.report, ensure_ascii=False, indent=2))
    return 0 if smoke.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
