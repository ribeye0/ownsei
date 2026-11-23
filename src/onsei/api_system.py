"""System and model-management HTTP routes, independent of project storage."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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

    return start_download(body.models)


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
