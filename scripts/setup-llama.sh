#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
release=b11541
base="https://github.com/ggml-org/llama.cpp/releases/download/$release"
mkdir -p .tools/downloads .tools/llama
python_bin=.venv/bin/python
"$python_bin" scripts/download_release.py "$base/llama-$release-bin-ubuntu-cuda-12.8-x64.tar.gz" .tools/downloads/llama.tar.gz 551ee13d02dd42599861c8ffbd71c7a110f379a51cb9c5947cb799cdbbc610b2
"$python_bin" scripts/download_release.py "$base/cudart-llama-$release-bin-ubuntu-cuda-12.8-x64.tar.gz" .tools/downloads/cudart.tar.gz 6c8acf749cb80a5932b8c5ba16476c3626ce72b09bcfe9078c27b1087ae39c4f
 tar -xzf .tools/downloads/llama.tar.gz -C .tools/llama
 tar -xzf .tools/downloads/cudart.tar.gz -C .tools/llama
printf 'llama.cpp %s CUDA 12.8 ready\n' "$release"
