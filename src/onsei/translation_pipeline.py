"""One translation pass with optional name extraction and isolated result writes."""

from contextlib import ExitStack
import time

from onsei.models import DEFAULT_NAMES, REGISTRY, TRANSLATION_MODELS
from onsei.inference import Llama
from onsei.translation import translate
from onsei.translation_config import model_config, model_prompt_version
from onsei.translation_runs import TranslationSession, source_key
from onsei.preferences import pin_translation_settings, updated_preferences
from onsei.context import translation_options as resolve_options


def process_translation(
    store,
    track_id,
    model,
    progress,
    *,
    snapshot=None,
    outputs=None,
    selected=None,
    translation_settings=None,
    translation_context=None,
    translation_options=None,
):
    if model not in TRANSLATION_MODELS:
        raise ValueError("Unsupported translation model")
    snapshot = snapshot or store.input_snapshot(track_id)
    settings = dict(snapshot["settings"])
    if translation_context is not None:
        settings.update(translation_context)
    if translation_settings:
        settings.update(updated_preferences(settings, translation_settings))
        settings.update(translation_settings)
    if translation_options is not None:
        settings["translation_options"] = translation_options
    settings["translation_options"] = resolve_options(settings, model)
    settings.update(pin_translation_settings(settings))
    snapshot = {**snapshot, "settings": settings}
    snapshot["source_key"] = source_key(snapshot)
    outputs = dict(outputs or {})
    previous = store.run_metadata(outputs["draft"]) if outputs.get("draft") else {}
    config, prompt = model_config(model, settings), model_prompt_version(model)
    if previous.get("prompt_version", prompt) != prompt or previous.get("config", config) != config:
        raise ValueError("翻譯設定或 prompt 已更新，請另建翻譯")
    identity = None
    stack = ExitStack()
    current, current_model = None, None
    started = time.monotonic()

    def engine_for(name):
        nonlocal stack, current, current_model
        if current_model != name:
            stack.close()
            stack = ExitStack()
            directory = store.path / "cache" / track_id / "pipeline" / name
            log = directory / "llama.log"
            progress(
                {
                    "stage": "translation",
                    "log_source": str(log),
                    "log_start": log.stat().st_size if log.exists() else 0,
                    "message": f"載入 {name}",
                }
            )
            current = stack.enter_context(
                Llama(name, directory, translation_options=settings["translation_options"])
            )
            current_model = name
        return current

    class EngineHandle:
        def __init__(self, name):
            self.model = name

        def generate(self, data):
            return engine_for(self.model).generate(data)

        def complete_json(self, *args, **kwargs):
            return engine_for(self.model).complete_json(*args, **kwargs)

        def name_engine(self):
            from onsei.modelmanager import installed

            return EngineHandle(DEFAULT_NAMES) if installed(DEFAULT_NAMES) else self

        @property
        def config(self):
            return model_config(self.model, settings)

        def record(self, *args, **kwargs):
            if current_model == self.model:
                current.record(*args, **kwargs)

        def execution_metadata(self):
            return engine_for(self.model).execution_metadata()

        def reject(self, error):
            return engine_for(self.model).reject(error)

    try:
        identity = store.new_translation_run(
            snapshot, model, identity=outputs.get("draft"), selected=selected
        )
        outputs["draft"] = identity
        store.update_run(
            identity,
            status="running",
            execution_status="running",
            error=None,
            prompt_version=prompt,
            config=config,
            model_revision=REGISTRY[model][1],
            translation_options=settings["translation_options"],
        )
        progress({"stage": "translation", "outputs": outputs, "message": "翻譯結果已建立", "fraction": 0})
        session = TranslationSession(store, snapshot, identity)
        translate(session, track_id, model, selected, progress, engine=EngineHandle(model))
        if store.run_metadata(identity).get("status") == "stale":
            raise ValueError("來源已更新，過期結果保留，請另建翻譯")
        cues = session.cues(track_id)
        done = [c.id for c in cues if c.zh and c.translation_status in ("translated", "manual")]
        store.update_run(
            identity,
            status="completed" if len(done) == len(cues) else "partial",
            execution_status="completed",
            error=None,
            coverage=done,
        )
        progress(
            {
                "stage": "translation",
                "fraction": 1,
                "outputs": outputs,
                "result_id": identity,
                "message": f"翻譯完成：{time.monotonic() - started:.1f} 秒",
            }
        )
        return outputs
    except BaseException as error:
        if identity:
            try:
                stale = store.run_metadata(identity).get("status") == "stale" or not store.snapshot_valid(
                    snapshot
                )
                store.update_run(
                    identity,
                    status="stale" if stale else "partial",
                    execution_status="cancelled"
                    if isinstance(error, (InterruptedError, KeyboardInterrupt))
                    else "failed",
                    error=str(error),
                )
            except ValueError:
                pass
        raise
    finally:
        stack.close()
