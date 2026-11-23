"""Console output for the local subtitle server."""

from contextlib import nullcontext
from copy import deepcopy
from importlib.metadata import version
import logging
import os

import uvicorn
from filelock import Timeout
from uvicorn.config import LOGGING_CONFIG, STARTUP_FAILURE

from onsei.update_install import install_gate, update_dir
from onsei.updater import RESTART_EXIT, updates
from onsei.util import ROOT
from onsei.user_paths import config_dir


LOGO = r"""
   ___                         _
  / _ \__      ___ __  ___  ___(_)
 | | | \ \ /\ / / '_ \/ __|/ _ \ |
 | |_| |\ V  V /| | | \__ \  __/ |
  \___/  \_/\_/ |_| |_|___/\___|_|
""".strip("\n")


class HttpFailures(logging.Filter):
    def filter(self, record):
        # Both Uvicorn HTTP backends put the numeric status last in access records.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[-1], int):
            return args[-1] >= 400
        return True


class ConsoleServer(uvicorn.Server):
    async def on_tick(self, counter):
        if updates.status()["phase"] == "installing":
            return True
        return await super().on_tick(counter)

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            port = self.servers[0].sockets[0].getsockname()[1]
            print(f"已就緒   http://127.0.0.1:{port}/\n轉錄與翻譯進度請見網頁的「任務紀錄」。按 Ctrl+C 停止服務。", flush=True)


def run_server(host: str, port: int, verbose: bool):
    config = deepcopy(LOGGING_CONFIG)
    if not verbose:
        config["loggers"]["uvicorn.error"]["level"] = "WARNING"
        config["filters"] = {"http_failures": {"()": HttpFailures}}
        config["handlers"]["access"]["filters"] = ["http_failures"]
        config["formatters"]["access"]["fmt"] = "%(asctime)s  HTTP %(status_code)s  %(request_line)s"
        config["formatters"]["access"]["datefmt"] = "%H:%M:%S"

    print(
        f"{LOGO}\n\nOwnsei {version('ownsei')}  |  日語音聲字幕工具\n"
        f"使用者設定  {config_dir()}\n"
        f"專案目錄  {ROOT / 'workspace'}\n正在啟動後端…",
        flush=True,
    )
    server = ConsoleServer(
        uvicorn.Config("onsei.api:app", host=host, port=port, timeout_graceful_shutdown=3, log_config=config)
    )
    try:
        lock = nullcontext()
        if (ROOT / "release.json").exists() and not os.environ.get("OWNSEI_UPDATE_LAUNCHER"):
            update_dir(ROOT).mkdir(parents=True, exist_ok=True)
            lock = install_gate(ROOT).read_lock()
        with lock:
            if not updates.managed and (update_dir(ROOT) / "backup/plan.json").exists():
                raise SystemExit("正在切換版本，請稍後再啟動")
            server.run()
    except Timeout:
        raise SystemExit("正在安裝更新，請稍後再啟動")
    except KeyboardInterrupt:
        pass
    if not server.started:
        raise SystemExit(STARTUP_FAILURE)
    print("Ownsei 後端已停止", flush=True)
    if updates.status()["phase"] == "installing":
        raise SystemExit(RESTART_EXIT)
