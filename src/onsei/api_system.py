"""System and model-management HTTP routes, independent of project storage."""

import json
from typing import ClassVar

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from filelock import FileLock
from pydantic import BaseModel, JsonValue

from onsei.user_paths import config_dir
from onsei.util import atomic_json

router = APIRouter(tags=["system"])


@router.get("/api/system")
def system_status():
    from onsei.environment import inspect_environment

    return inspect_environment()


@router.get("/api/devices")
def execution_devices():
    from onsei.backend import inventory

    return inventory()


@router.get("/api/models")
def model_catalog():
    from onsei.modelmanager import catalog

    return catalog()


class ModelDownloads(BaseModel):
    models: list[str]


@router.post("/api/models/download")
def model_download(body: ModelDownloads):
    from onsei.modelmanager import start_download
    from onsei.diskspace import InsufficientSpace

    try:
        return start_download(body.models)
    except InsufficientSpace as error:
        raise HTTPException(507, str(error)) from error


@router.get("/api/models/{name}/log")
def model_download_log(name: str):
    from onsei.modelmanager import job_log

    return {"text": job_log(name)}


@router.get("/api/models/{name}/log/download")
def model_full_log(name: str):
    from onsei.modelmanager import log_path

    path = log_path(name)
    if path is None:
        raise HTTPException(404, "模型紀錄不存在")
    return FileResponse(path, filename=f"{name}-{path.stem[:8]}.log", media_type="text/plain")


@router.post("/api/models/{name}/cancel")
def model_download_cancel(name: str):
    from onsei.modelmanager import cancel_download

    return cancel_download(name)


@router.delete("/api/models/{name}")
def model_delete(name: str):
    from onsei.modelmanager import delete_model

    return delete_model(name)


class InterfacePreferences(BaseModel):
    keys: ClassVar[set[str]] = {
        "locale",
        "theme",
        "export",
        "execution",
        "vad",
        "asr_filter",
        "follow_playback",
        "playback_rate",
    }
    values: dict[str, JsonValue]
    import_missing: bool = False


@router.post("/api/preferences/interface")
def interface_preferences(body: InterfacePreferences):
    if body.values.keys() - body.keys or len(json.dumps(body.values)) > 16384:
        raise ValueError("無效的介面設定")
    path = config_dir() / "interface.json"
    with FileLock(str(path) + ".lock", timeout=5):
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(current, dict):
            raise ValueError("介面設定檔格式無效，請檢查 interface.json")
        merged = {**body.values, **current} if body.import_missing else {**current, **body.values}
        if merged != current:
            atomic_json(path, merged)
        return merged
