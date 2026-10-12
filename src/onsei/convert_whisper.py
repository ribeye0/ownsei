"""Run in the conversion runtime, with pinned local source weights and no remote code."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path


def convert(source: Path, destination: Path):
    from ctranslate2.converters import TransformersConverter

    destination.parent.mkdir(parents=True, exist_ok=True)
    # A failed conversion must not leave a partially usable inference checkpoint.
    with tempfile.TemporaryDirectory(prefix=".whisper-convert-", dir=destination.parent) as temporary:
        output = Path(temporary) / "model"
        converter = TransformersConverter(
            str(source),
            load_as_float16=True,
            copy_files=["tokenizer.json", "preprocessor_config.json", "generation_config.json"],
        )
        converter.convert(str(output), quantization="int8_float16")
        if destination.exists():
            shutil.rmtree(destination)
        output.rename(destination)


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
