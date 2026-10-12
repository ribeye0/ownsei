"""Translation/reference/review orchestration with one resident GPU model at a time."""

from contextlib import ExitStack
import time

from onsei.models import DEFAULT_REVIEW, REGISTRY
from onsei.review import review
from onsei.translation import CONFIG, model_prompt_version, Llama, translate
from onsei.translation_policy import POLICY_VERSION
from onsei.translation_reference import build_reference
from onsei.translation_runs import TranslationSession, source_key
from onsei.preferences import pin_translation_settings, updated_preferences


def process_translation(store, track_id, model, progress, *, snapshot=None, review_only=False,
                        after_review=False, use_reference=False, review_model=DEFAULT_REVIEW,
                        outputs=None, selected=None, translation_settings=None, translation_context=None):
    snapshot = snapshot or store.input_snapshot(track_id)
    if translation_context is not None:
        snapshot = {**snapshot, "settings": {**snapshot["settings"], **translation_context}}
        snapshot["source_key"] = source_key(snapshot)
    if review_only:
        from onsei.preferences import translation_preferences
        translation_settings = translation_preferences(store.run_metadata(snapshot["result_id"]))
    if translation_settings:
        snapshot = {**snapshot, "settings": {**snapshot["settings"],
                                           **updated_preferences(snapshot["settings"], translation_settings),
                                           **translation_settings}}
    snapshot = {**snapshot, "settings": {**snapshot["settings"], **pin_translation_settings(snapshot["settings"])}}
    outputs = dict(outputs or {})
    phases = (["reference"] if use_reference else []) + ([] if review_only else ["translation"]) + (
        ["review"] if after_review or review_only else [])
    if not phases:
        raise ValueError("No translation phase selected")
    started = time.monotonic()
    stack = ExitStack()
    current, current_model = None, None
    phase_index = 0

    def emit(event):
        event = dict(event)
        if "fraction" in event:
            event["fraction"] = (phase_index + event["fraction"]) / len(phases)
        progress(event)

    def engine_for(name):
        nonlocal current, current_model, stack
        if current_model != name:
            stack.close()
            stack = ExitStack()
            directory = store.path / "cache" / track_id / "pipeline" / name
            log = directory / "llama.log"
            emit({"stage": phases[phase_index], "log_source": str(log),
                  "log_start": log.stat().st_size if log.exists() else 0, "message": f"載入 {name}"})
            current = stack.enter_context(Llama(name, directory))
            current_model = name
        return current

    class EngineHandle:
        def __init__(self, name):
            self.model = name
            self.summary = None
            self.fallbacks = []

        def generate(self, data):
            return engine_for(self.model).generate(data)

        def complete_json(self, *args, **kwargs):
            return engine_for(self.model).complete_json(*args, **kwargs)

        def record(self, *args, **kwargs):
            if current_model == self.model:
                current.record(*args, **kwargs)

        def execution_metadata(self):
            return engine_for(self.model).execution_metadata()

        def reject(self, error):
            return engine_for(self.model).reject(error)

    handles = {}

    def handle(name):
        if name not in handles:
            handles[name] = EngineHandle(name)
        return handles[name]

    def announce(stage):
        nonlocal phase_index
        phase_index = phases.index(stage)
        emit({"stage": stage, "pipeline_stage": stage, "fraction": 0,
              "message": {"reference": "整理全文參照", "translation": "生成初譯", "review": "全文雙語審校"}[stage]})

    identity = None
    try:
        facts = []
        if use_reference:
            announce("reference")
            facts = build_reference(store, snapshot, handle(review_model), emit)
        draft = snapshot["result_id"]
        if not review_only:
            announce("translation")
            identity = store.new_translation_run(snapshot, model, identity=outputs.get("draft"), selected=selected)
            outputs["draft"] = draft = identity
            emit({"outputs": outputs, "message": "初譯結果已建立", "stage": "translation"})
            previous = store.run_metadata(identity)
            prompt_version = model_prompt_version(model)
            if previous.get("prompt_version", prompt_version) != prompt_version:
                raise ValueError("續跑的 prompt 已更新，請另建新翻譯")
            store.update_run(identity, status="running", prompt_version=prompt_version,
                             policy_version=POLICY_VERSION, model_revision=REGISTRY[model][1], config=CONFIG,
                             reference=facts)
            session = TranslationSession(store, snapshot, identity)
            translate(session, track_id, model, selected, emit, engine=handle(model), reference=facts)
            if store.run_metadata(identity).get("status") == "stale":
                raise ValueError("來源已更新，過期初譯保留，請另建翻譯")
            cues = session.cues(track_id)
            done = [c.id for c in cues if c.zh and c.translation_status in ("translated", "manual")]
            store.update_run(identity, status="completed" if len(done) == len(cues) else "partial", coverage=done)
        if after_review or review_only:
            announce("review")
            parent = store.input_snapshot(track_id, snapshot["version"], draft)
            parent["settings"] = {**parent["settings"], **{k: snapshot["settings"][k] for k in ("target_language", "translation_style", "global_glossary") if k in snapshot["settings"]}}
            identity = store.new_translation_run(parent, review_model, stage="review", identity=outputs.get("review"))
            outputs["review"] = identity
            emit({"outputs": outputs, "stage": "review", "message": "審校稿已建立，初譯保留"})
            store.update_run(identity, reference=facts, model_revision=REGISTRY[review_model][1])
            review(store, parent, identity, handle(review_model), emit, reference=facts)
        emit({"outputs": outputs, "stage": phases[-1], "fraction": 1,
              "message": f"翻譯流程完成：{time.monotonic() - started:.1f} 秒", "result_id": identity})
    except BaseException as error:
        if identity:
            try:
                metadata = store.run_metadata(identity)
                stale = metadata.get("status") == "stale" or not store.snapshot_valid(snapshot)
                store.update_run(identity, status="stale" if stale else "partial",
                                 execution_status="cancelled" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "failed",
                                 error=str(error))
            except ValueError:
                pass
        raise
    finally:
        stack.close()
    return outputs
