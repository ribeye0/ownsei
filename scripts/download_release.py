"""Resume pinned artifacts with HTTP ranges, persistent connections and SHA256 verification."""

import concurrent.futures
import hashlib
import json
import sys
import threading
import time
import shutil
from pathlib import Path

import httpx

url, output = sys.argv[1:3]
expected = sys.argv[3] if len(sys.argv) > 3 else None
output = Path(output)
if expected and output.is_file():
    with output.open("rb") as existing:
        actual = hashlib.file_digest(existing, "sha256").hexdigest()
    if actual == expected:
        print(
            json.dumps(
                {"file": str(output), "bytes": output.stat().st_size, "sha256": actual, "cached": True}
            ),
            flush=True,
        )
        sys.exit(0)
workers = 8
client = httpx.Client(
    follow_redirects=True,
    timeout=httpx.Timeout(60, connect=10),
    limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers, keepalive_expiry=60),
)
r = client.head(url)
r.raise_for_status()
size = int(r.headers["Content-Length"])
final_url = str(r.url)
print(json.dumps({"stage": "download", "file": str(output), "bytes_done": 0, "bytes_total": size}), flush=True)
legacy_parts = output.with_suffix(output.suffix + ".parts")
parts = output.with_suffix(output.suffix + ".parts-8m")
parts.mkdir(exist_ok=True)
block = 8 * 1024 * 1024
guard = threading.Lock()
completed_bytes = 0
last_report = 0.0


def mark_ready(count):
    global completed_bytes, last_report
    with guard:
        completed_bytes += count
        if time.monotonic() - last_report >= .5 or completed_bytes == size:
            print(json.dumps({"stage": "download", "bytes_done": completed_bytes, "bytes_total": size}), flush=True)
            last_report = time.monotonic()


def fetch(index):
    p = parts / str(index)
    start = index * block
    end = min(size, start + block) - 1
    if p.exists() and p.stat().st_size == end - start + 1:
        mark_ready(p.stat().st_size)
        return p
    # Reuse complete ranges left by the earlier 512 KiB downloader.
    old_block = 512 * 1024
    old_parts = [legacy_parts / str(i) for i in range(start // old_block, end // old_block + 1)]
    if all(
        x.exists() and x.stat().st_size == min(old_block, size - int(x.name) * old_block) for x in old_parts
    ):
        p.write_bytes(b"".join(x.read_bytes() for x in old_parts))
        mark_ready(p.stat().st_size)
        return p
    for attempt in range(4):
        try:
            with client.stream("GET", final_url, headers={"Range": f"bytes={start}-{end}"}) as response:
                response.raise_for_status()
                if response.status_code == 200:
                    raise RangeUnsupported("Server requires a sequential download")
                if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                    raise ValueError("Server did not honor exact byte range")
                content = response.read()
                if len(content) != end - start + 1:
                    raise ValueError("Truncated response")
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(content)
            tmp.replace(p)
            mark_ready(p.stat().st_size)
            return p
        except (httpx.HTTPError, ValueError):
            if attempt == 3:
                raise


class RangeUnsupported(Exception):
    pass


temporary = output.with_suffix(output.suffix + ".assembling")
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        ordered = list(pool.map(fetch, range((size + block - 1) // block)))
    with temporary.open("wb") as f:
        for p in ordered:
            f.write(p.read_bytes())
except RangeUnsupported:
    completed_bytes = 0
    with client.stream("GET", final_url) as response, temporary.open("wb") as f:
        response.raise_for_status()
        for chunk in response.iter_bytes(1024 * 1024):
            f.write(chunk)
            mark_ready(len(chunk))
    if temporary.stat().st_size != size:
        raise ValueError("Truncated sequential download")
finally:
    client.close()
with temporary.open("rb") as f:
    actual = hashlib.file_digest(f, "sha256").hexdigest()
if expected and actual != expected:
    temporary.unlink(missing_ok=True)
    shutil.rmtree(parts, ignore_errors=True)
    shutil.rmtree(legacy_parts, ignore_errors=True)
    raise ValueError("Artifact SHA256 mismatch; cached ranges cleared, retry download")
temporary.replace(output)
print(json.dumps({"file": str(output), "bytes": size, "sha256": actual}), flush=True)
