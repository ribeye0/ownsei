"""Hash-pinned engine inputs shared by installation and release packaging."""

ASSETS = {
    "cuda-linux": {
        "name": "cudart-llama-b11541-bin-ubuntu-cuda-13.4-x64.tar.gz",
        "repo": "ggml-org/llama.cpp",
        "sha256": "4d6dba50d1d16b3987b6a8cff73a58338ea1eba7ded1d3295ea174b4f515a746",
        "size": 440236543,
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/cudart-llama-b11541-bin-ubuntu-cuda-13.4-x64.tar.gz",
    },
    "cuda-windows": {
        "name": "cudart-llama-bin-win-cuda-13.4-x64.zip",
        "repo": "ggml-org/llama.cpp",
        "sha256": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
        "size": 423535356,
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/cudart-llama-bin-win-cuda-13.4-x64.zip",
    },
    "llama-linux-cuda": {
        "name": "llama-b11541-bin-ubuntu-cuda-13.4-x64.tar.gz",
        "repo": "ggml-org/llama.cpp",
        "sha256": "2d6f7589eb46f80905cd8fadcc41c67a0a5f5d6fe96ddd4fb37fd7a73a0872d5",
        "size": 152841931,
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/llama-b11541-bin-ubuntu-cuda-13.4-x64.tar.gz",
    },
    "llama-linux-vulkan": {
        "name": "llama-b11541-bin-ubuntu-vulkan-x64.tar.gz",
        "repo": "ggml-org/llama.cpp",
        "sha256": "bf91ce14ddfda55c01c8b8846aa7d6093a07b0213913b6216ed4a2d6ea78b1f8",
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/llama-b11541-bin-ubuntu-vulkan-x64.tar.gz",
    },
    "llama-windows-cuda": {
        "name": "llama-b11541-bin-win-cuda-13.4-x64.zip",
        "repo": "ggml-org/llama.cpp",
        "sha256": "927672eae5bb9cf4dde89a00a764c33e69e98db4b3d8be42f8990bf383cfdf61",
        "size": 153464749,
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/llama-b11541-bin-win-cuda-13.4-x64.zip",
    },
    "llama-windows-vulkan": {
        "name": "llama-b11541-bin-win-vulkan-x64.zip",
        "repo": "ggml-org/llama.cpp",
        "sha256": "37bba511d120222f13c3d520dd875f739a530cc124e78f7cbce0026de0173d8a",
        "size": 33464528,
        "tag": "b11541",
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11541/llama-b11541-bin-win-vulkan-x64.zip",
    },
    "speech-linux": {
        "name": "speech-0.11.2-linux-x64-vulkan.zip",
        "repo": "nyosegawa/speech.cpp",
        "sha256": "28e6841c650c8a4d7c5be06b8e34eb2f14d43f8b26d52fc73f24b504875dc12d",
        "tag": "v0.11.2",
        "url": "https://github.com/nyosegawa/speech.cpp/releases/download/v0.11.2/speech-0.11.2-linux-x64-vulkan.zip",
    },
    "speech-windows": {
        "name": "speech-0.11.2-windows-x64-vulkan.zip",
        "repo": "nyosegawa/speech.cpp",
        "sha256": "2cf46f0351a2a90b9ce0bfb83cf63ac209ca31c88df9a5bc0126898a20a58586",
        "size": 29726411,
        "tag": "v0.11.2",
        "url": "https://github.com/nyosegawa/speech.cpp/releases/download/v0.11.2/speech-0.11.2-windows-x64-vulkan.zip",
    },
}


def engine_assets(platform, backend):
    if platform not in ("linux", "windows") or backend not in ("cuda", "vulkan"):
        raise ValueError("Expected linux/windows and cuda/vulkan")
    return {
        "speech": [ASSETS["speech-" + platform]],
        "llama": [ASSETS[f"llama-{platform}-{backend}"]]
        + ([ASSETS["cuda-" + platform]] if backend == "cuda" else []),
    }
