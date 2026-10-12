from __future__ import annotations

import json
from pathlib import Path

import typer

from onsei.audio import import_audio
from onsei.models import DEFAULT_REVIEW, DEFAULT_TRANSLATION, REGISTRY, prepare
from onsei.storage import Store
from onsei.util import ROOT

app = typer.Typer(name="ownsei", no_args_is_help=True, pretty_exceptions_enable=False)
models = typer.Typer(no_args_is_help=True)
app.add_typer(models, name="models")


@app.command()
def doctor(strict: bool = False, as_json: bool = typer.Option(False, "--json"), runtime_check: bool = False, prerequisites: bool = False):
    from onsei.environment import inspect_environment
    report = inspect_environment(runtimes=runtime_check, engines=not prerequisites)
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            status = "OK" if check["ok"] else "MISSING" if check["required"] else "INFO"
            typer.echo(f"[{status}] {check['name']}: {check['detail']}")
        typer.echo(f"Project root: {ROOT}")
    if strict and not report["ready"]:
        raise typer.Exit(1)


@models.command("list")
def model_list():
    for k, (repo, rev, _) in REGISTRY.items():
        typer.echo(f"{k}: {repo} @ {rev[:12]}")


@models.command("prepare")
def model_prepare(model: str = typer.Option("whisper-ja-760m")):
    typer.echo(str(prepare(model)))


@app.command()
def init(project: Path, title: str = "未命名作品"):
    typer.echo(str(Store.create(project, title).path))


@app.command("import")
def import_cmd(project: Path, source: Path):
    for t in import_audio(Store(project), source):
        typer.echo(f"{t.id} {t.name} {t.duration_ms / 1000:.2f}s")


@app.command()
def tracks(project: Path):
    for t in Store(project).tracks():
        typer.echo(json.dumps(t.model_dump(), ensure_ascii=False))


def launch(
    project: Path,
    action: str,
    asr: str,
    translation: str,
    track: str | None,
    limit_seconds: float | None,
    cue: list[str] | None = None,
    extra_alignment: bool = False,
    after_review: bool = False,
    use_reference: bool = False,
    review_model: str = DEFAULT_REVIEW,
):
    from onsei.runner import run_job

    store = Store(project)
    job = store.new_job(
        action,
        {
            "asr": asr,
            "translation": translation,
            "track_ids": [track] if track else None,
            "limit_ms": round(limit_seconds * 1000) if limit_seconds else None,
            "cue_ids": cue,
            "extra_alignment": extra_alignment,
            "after_review": after_review,
            "use_reference": use_reference,
            "review_model": review_model,
        },
    )
    run_job(store, job["id"])
    result = store.job(job["id"])
    typer.echo(json.dumps(result, ensure_ascii=False))
    if result["status"] != "completed":
        raise typer.Exit(1)


@app.command()
def transcribe(
    project: Path,
    asr: str = "whisper-ja-760m",
    track: str | None = None,
    limit_seconds: float | None = None,
    extra_alignment: bool = False,
):
    launch(
        project, "transcribe", asr, DEFAULT_TRANSLATION, track, limit_seconds, extra_alignment=extra_alignment
    )


@app.command()
def translate(
    project: Path,
    model: str = DEFAULT_TRANSLATION,
    track: str | None = None,
    cue: list[str] = typer.Option(None),
    after_review: bool = False,
    use_reference: bool = False,
    review_model: str = DEFAULT_REVIEW,
):
    if cue and after_review:
        raise typer.BadParameter("局部重譯不能搭配全文審校，請先完成整份初譯")
    launch(project, "translate", "whisper-ja-760m", model, track, None, cue,
           after_review=after_review, use_reference=use_reference, review_model=review_model)


@app.command()
def review(project: Path, model: str = DEFAULT_REVIEW, track: str | None = None, use_reference: bool = False):
    """Review the selected Chinese result, keeping its draft intact."""
    launch(project, "review", "whisper-ja-760m", model, track, None,
           use_reference=use_reference, review_model=model)


@app.command()
def realign(project: Path, track: str | None = None, cue: list[str] = typer.Option(None)):
    launch(project, "realign", "whisper-ja-760m", DEFAULT_TRANSLATION, track, None, cue)


@app.command()
def run(
    project: Path,
    asr: str = "whisper-ja-760m",
    translation: str = DEFAULT_TRANSLATION,
    track: str | None = None,
    limit_seconds: float | None = None,
    extra_alignment: bool = False,
    after_review: bool = False,
    use_reference: bool = False,
    review_model: str = DEFAULT_REVIEW,
):
    launch(project, "run", asr, translation, track, limit_seconds, extra_alignment=extra_alignment,
           after_review=after_review, use_reference=use_reference, review_model=review_model)


@app.command()
def export(project: Path, track: str | None = None, japanese_only: bool = False):
    from onsei.export import export_track

    store = Store(project)
    for t in store.tracks():
        if not track or track == t.id:
            for p in export_track(store, t.id, store.path / "exports", japanese_only):
                typer.echo(str(p))


@app.command("import-transcript")
def import_transcript(project: Path, source: Path):
    from onsei.domain import Cue

    store = Store(project)
    cues = [
        Cue.model_validate_json(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cues or len({(c.track_id, c.version) for c in cues}) != 1:
        raise ValueError("JSONL must contain exactly one track/version")
    track = store.track(cues[0].track_id)
    if store.cues(track.id, cues[0].version):
        raise ValueError(
            "Version already exists; import into a project with the original audio and no matching transcript"
        )
    if any(c.end_ms > track.duration_ms for c in cues):
        raise ValueError("Transcript timestamps exceed audio duration")
    store.replace_cues(track, cues[0].version, cues)
    typer.echo(f"Imported {len(cues)} cues")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn

    if host != "127.0.0.1":
        raise ValueError("MVP server only binds to 127.0.0.1")
    uvicorn.run("onsei.api:app", host=host, port=port, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    app()
