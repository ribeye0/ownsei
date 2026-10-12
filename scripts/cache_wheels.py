"""Pre-fetch locked wheels with parallel ranges on slow per-connection links."""

import concurrent.futures
import json
import hashlib
import time
import html
from urllib.parse import urlsplit, unquote, parse_qs
import httpx
import re
import subprocess
import sys
import urllib.request
import socket

from pathlib import Path

from filelock import FileLock
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

socket.setdefaulttimeout(20)

rank = {tag: n for n, tag in enumerate(sys_tags())}
root = Path(__file__).resolve().parents[1]
directory = root / ".tools/wheels"
directory.mkdir(exist_ok=True)
spec = Path(sys.argv[1]).read_text()
packages = re.findall(r"^([a-zA-Z0-9._-]+)==([^\s;]+)", spec, re.M)
if len(sys.argv) > 2:
    packages = [p for p in packages if p[0] in sys.argv[2:]]


def download(pkg):
    with FileLock(directory / (pkg[0] + "-" + pkg[1] + ".lock")):
        for attempt in range(4):
            try:
                download_locked(pkg)
                return
            except (OSError, subprocess.SubprocessError) as error:
                print("Retry", pkg[0], str(error), flush=True)
                if attempt == 3:
                    raise
                time.sleep(2)


def download_locked(pkg):
    name, version = pkg
    if name == "torch" and any(directory.glob("torch-2.10.0+cu128-*.whl")):
        return
    metadata = directory / f"{name}-{version}.metadata.json"
    if metadata.exists():
        files = json.loads(metadata.read_text())
    elif name.startswith("nvidia-"):
        response = httpx.get(f"https://download.pytorch.org/whl/cu128/{name}/", timeout=20)
        response.raise_for_status()
        files = []
        for link in re.findall(r'href="([^"]+)"', response.text):
            url = html.unescape(link)
            parsed = urlsplit(url)
            filename = unquote(parsed.path.rsplit("/", 1)[-1])
            if not filename.endswith(".whl"):
                continue
            _, candidate_version, _, _ = parse_wheel_filename(filename)
            hashes = parse_qs(parsed.fragment)
            if str(candidate_version) == version and "sha256" in hashes:
                files.append(
                    {
                        "filename": filename,
                        "size": None,
                        "url": url.split("#")[0],
                        "digests": {"sha256": hashes["sha256"][0]},
                    }
                )
        if not files:
            raise ValueError(f"No official CUDA wheel for {name} {version}")
        metadata.write_text(json.dumps(files))
    else:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as r:
            files = json.load(r)["urls"]
        metadata.write_text(json.dumps(files))
    choices = []
    for f in files:
        if f["filename"].endswith(".whl"):
            tags = parse_wheel_filename(f["filename"])[3]
            score = min((rank[t] for t in tags if t in rank), default=10**9)
            if score < 10**9:
                choices.append((score, f))
    if not choices:
        print("No wheel:", name, flush=True)
        return
    f = min(choices, key=lambda x: x[0])[1]
    path = directory / f["filename"]
    if path.exists() and (f["size"] is None or path.stat().st_size == f["size"]):
        with path.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() == f["digests"]["sha256"]:
                return
    artifact_url = (
        f"https://pypi.nvidia.com/{name}/{f['filename']}" if name.startswith("nvidia-") else f["url"]
    )
    print("Downloading", f["filename"], flush=True)
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/download_release.py"),
            artifact_url,
            str(path),
            f["digests"]["sha256"],
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print("Ready", name, flush=True)


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(download, packages))
