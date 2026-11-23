"""Local application and model-management commands."""

import json
from pathlib import Path
import typer
from onsei.audio import import_audio
from onsei.models import ASR_MODELS, DEFAULT_ASR, DEFAULT_TRANSLATION, REGISTRY, TRANSLATION_MODELS, prepare
from onsei.storage import Store
from onsei.util import ROOT

app = typer.Typer(name="ownsei", no_args_is_help=True, pretty_exceptions_enable=False)
models = typer.Typer(no_args_is_help=True)
app.add_typer(models, name="models")


@app.command()
def doctor(strict: bool = False, as_json: bool = typer.Option(False, "--json"), runtime_check: bool = False):
    from onsei.environment import inspect_environment

    report = inspect_environment(runtimes=runtime_check)
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for c in report["checks"]:
            typer.echo(
                f"[{'OK' if c['ok'] else 'ERROR' if c['required'] else 'INFO'}] {c['name']}: {c['detail']}"
            )
        typer.echo(f"Project root: {ROOT}")
    if strict and not report["ready"]:
        raise typer.Exit(1)


@models.command("list")
def model_list():
    for name, (repo, revision, _) in REGISTRY.items():
        typer.echo(f"{name}: {repo} @ {revision[:12]}")


@models.command("prepare")
def model_prepare(model: str = DEFAULT_ASR):
    typer.echo(str(prepare(model)))


@app.command("runtime")
def prepare_runtime(kind: str):
    """Prepare the pinned FireRedVAD CPU runtime."""
    from onsei.models import setup_runtime

    if kind != "vad":
        raise typer.BadParameter("Supported: vad")
    setup_runtime(kind)


@app.command()
def init(project: Path, title: str = "未命名專案"):
    typer.echo(str(Store.create(project, title).path))


@app.command("import")
def import_cmd(project: Path, source: Path):
    for track in import_audio(Store(project), source):
        typer.echo(json.dumps(track.model_dump(), ensure_ascii=False))


@app.command()
def tracks(project: Path):
    for track in Store(project).tracks():
        typer.echo(json.dumps(track.model_dump(), ensure_ascii=False))


def launch(project, action, asr, translation, track, limit_seconds=None, cue=None, options=None):
    from onsei.runner import run_job

    if asr not in ASR_MODELS or translation not in TRANSLATION_MODELS:
        raise typer.BadParameter("Unsupported processing model")
    store = Store(project)
    job = store.new_job(
        action,
        {
            "asr": asr,
            "translation": translation,
            "track_ids": [track] if track else None,
            "cue_ids": cue,
            "limit_ms": round(limit_seconds * 1000) if limit_seconds else None,
            "translation_options": options,
        },
    )
    run_job(store, job["id"])
    result = store.job(job["id"])
    typer.echo(json.dumps(result, ensure_ascii=False))
    if result["status"] != "completed":
        raise typer.Exit(1)


@app.command()
def transcribe(
    project: Path, asr: str = DEFAULT_ASR, track: str | None = None, limit_seconds: float | None = None
):
    launch(project, "transcribe", asr, DEFAULT_TRANSLATION, track, limit_seconds)


@app.command()
def translate(
    project: Path,
    model: str = DEFAULT_TRANSLATION,
    track: str | None = None,
    cue: list[str] = typer.Option(None),
    context: int | None = None,
    names: bool | None = None,
    history: bool | None = None,
):
    options = {
        key: value
        for key, value in {"context": context, "names": names, "history": history}.items()
        if value is not None
    }
    launch(project, "translate", DEFAULT_ASR, model, track, cue=cue, options=options)


@app.command()
def run(
    project: Path,
    asr: str = DEFAULT_ASR,
    model: str = DEFAULT_TRANSLATION,
    track: str | None = None,
    context: int | None = None,
    names: bool | None = None,
    history: bool | None = None,
    limit_seconds: float | None = None,
):
    options = {
        key: value
        for key, value in {"context": context, "names": names, "history": history}.items()
        if value is not None
    }
    launch(project, "run", asr, model, track, limit_seconds, options=options)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8878,
    verbose: bool = typer.Option(
        False, "--verbose", help="Show all HTTP requests and server lifecycle logs."
    ),
):
    from onsei.server import run_server

    if host != "127.0.0.1":
        raise ValueError("Ownsei binds to 127.0.0.1")
    run_server(host, port, verbose)


if __name__ == "__main__":
    app()
