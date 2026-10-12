"""Optional range-download path; only fetches pinned public model artifacts."""

import concurrent.futures
import subprocess
import sys
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_url
from onsei.models import GGUF, REGISTRY, WHISPER_CONVERSIONS, model_dir
from onsei.util import file_hash

root = Path(__file__).resolve().parents[1]


def fetch(name):
    repo, revision, _ = REGISTRY[name]
    files = HfApi().model_info(repo, revision=revision, files_metadata=True).siblings
    for f in files:
        relevant = (
            f.rfilename == GGUF.get(name) if name in GGUF else f.rfilename.endswith((".safetensors", ".bin"))
        )
        if not relevant:
            continue
        directory = root / "models/.sources" / name if name in WHISPER_CONVERSIONS else model_dir(name)
        path = directory / f.rfilename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and f.lfs and file_hash(path) == f.lfs["sha256"]:
            continue
        print("Fetching", name, f.rfilename, f.size, flush=True)
        subprocess.run(
            [
                sys.executable,
                str(root / "scripts/download_release.py"),
                hf_hub_url(repo, f.rfilename, revision=revision),
                str(path),
                f.lfs["sha256"] if f.lfs else "",
            ],
            check=True,
        )


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(fetch, sys.argv[1:]))
