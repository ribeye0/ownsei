import json

import pytest

from onsei.worker import MultilingualWhisper, whisper_settings


def test_custom_vocabulary_generation_settings_preserve_remapped_ids(tmp_path):
    (tmp_path / "onsei-model.json").write_text(json.dumps({"compute_type": "int8_float16"}))
    (tmp_path / "generation_config.json").write_text(
        json.dumps({"is_multilingual": True, "suppress_tokens": [18871, 18974]})
    )
    assert whisper_settings(tmp_path) == ("int8_float16", True, {"suppress_tokens": [18871, 18974]})
    (tmp_path / "generation_config.json").write_text(json.dumps({"suppress_tokens": []}))
    assert whisper_settings(tmp_path)[2] == {"suppress_tokens": []}


def test_existing_ct2_checkpoint_keeps_fp16_and_default_suppression(tmp_path):
    assert whisper_settings(tmp_path) == ("float16", False, {})


def test_language_adapter_keeps_ct2_generation_encoding_and_alignment():
    class Backend:
        is_multilingual = False

        def generate(self, value):
            return value + 1

        def encode(self, value):
            return value + 2

        def align(self, value):
            return value + 3

    wrapped = MultilingualWhisper(Backend())
    assert wrapped.is_multilingual and not wrapped.model.is_multilingual
    assert [wrapped.generate(0), wrapped.encode(0), wrapped.align(0)] == [1, 2, 3]


def test_converted_checkpoint_reuse_verifies_tokenizer_and_weights(tmp_path, monkeypatch):
    import onsei.models as models
    from onsei.util import file_hash

    monkeypatch.setattr(models, "ROOT", tmp_path)
    name = "whisper-ja-anime-v0.3"
    path = models.model_dir(name)
    path.mkdir(parents=True)
    (path / "model.bin").write_bytes(b"converted weights")
    (path / "tokenizer.json").write_text("{}")
    (path / "onsei-model.json").write_text(
        json.dumps(
            {
                "repo": models.REGISTRY[name][0],
                "revision": models.REGISTRY[name][1],
                "conversion": models.WHISPER_CONVERSION_CONFIG,
                "sha256": {f: file_hash(path / f) for f in ["model.bin", "tokenizer.json"]},
            }
        )
    )

    def download(**kwargs):
        raise RuntimeError("must prepare again")

    monkeypatch.setattr(models, "snapshot_download", download)
    assert models.prepare_whisper(name) == path
    (path / "tokenizer.json").write_text("corrupted")
    with pytest.raises(RuntimeError, match="must prepare again"):
        models.prepare_whisper(name)


def test_unverified_source_never_reaches_converter(tmp_path, monkeypatch):
    import onsei.models as models

    monkeypatch.setattr(models, "ROOT", tmp_path)
    name = "whisper-ja-1.5b"
    source = tmp_path / "models/.sources" / name
    source.mkdir(parents=True)
    (source / "model.safetensors").write_bytes(b"wrong source")
    monkeypatch.setattr(models, "snapshot_download", lambda **_: None)
    monkeypatch.setattr(
        models, "setup_runtime", lambda *_: pytest.fail("unverified source reached converter")
    )
    with pytest.raises(ValueError, match="source SHA256 mismatch"):
        models.prepare_whisper(name)
