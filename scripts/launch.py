"""Run the local UI and open a browser after the server is ready."""

import argparse
import errno
import json
import shutil
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from filelock import FileLock, Timeout

DEFAULT_PORT = 8878


def select_port(requested=None):
    """Respect an explicit port; otherwise find an available loopback port."""
    candidates = [requested] if requested is not None else [*range(DEFAULT_PORT, min(DEFAULT_PORT + 20, 65536)), 0]
    for port in candidates:
        with socket.socket() as probe:
            if sys.platform != "win32":
                # Match asyncio's POSIX listener: recently closed connections
                # must not prevent restarting a server on the same port.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                if requested is not None or error.errno != errno.EADDRINUSE:
                    raise
            else:
                return probe.getsockname()[1]
    raise OSError("No available loopback port")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="Use this port (default: first available from 8878)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Show all HTTP requests and server lifecycle logs")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    root = Path(__file__).resolve().parents[1]
    try:
        port = select_port(args.port)
    except OSError as error:
        parser.error(f"Cannot listen on the requested port: {error}")
    if args.port is None and port != DEFAULT_PORT:
        print(f"Port {DEFAULT_PORT} is occupied; using {port}.", flush=True)
    env = {**os.environ, "PYTHONUTF8": "1", "ONSEI_ROOT": str(root)}
    command = [sys.executable, "-m", "onsei.cli", "serve", "--port", str(port)]
    if args.verbose:
        command.append("--verbose")
    from onsei.update_install import apply, install_gate, rollback, setup, update_dir
    from onsei.updater import RESTART_EXIT

    release = (root / "release.json").is_file()
    cache = update_dir(root)

    if not release:
        raise SystemExit(serve(command, root, env, port, not args.no_browser))
    cache.mkdir(parents=True, exist_ok=True)
    primary = FileLock(cache / "launcher.lock", timeout=0)
    try:
        primary.acquire()
    except Timeout:
        # Additional instances may keep using the same installation. Only the
        # primary launcher owns its download cache and can replace release files.
        raise SystemExit(serve(command, root, env, port, not args.no_browser))

    env["OWNSEI_UPDATE_LAUNCHER"] = "1"
    gate = install_gate(root)
    opening = not args.no_browser
    pending = False
    try:
        if (cache / "backup/plan.json").is_file():
            with gate.write_lock():
                rollback(root)
                setup(root)
                (cache / "error.txt").write_text("上次安裝中斷，已還原舊版", encoding="utf-8")
        while True:
            try:
                code = serve(command, root, env, port, opening, pending)
            except Exception as error:
                if not pending:
                    raise
                print(f"新版未能啟動：{error}", flush=True)
                code = 1
            if pending and (cache / "backup/plan.json").exists():
                rollback(root)
                setup(root)
                (cache / "error.txt").write_text("新版未能啟動，已還原舊版", encoding="utf-8")
                pending, opening = False, False
                continue
            if code != RESTART_EXIT:
                raise SystemExit(code)
            try:
                with gate.write_lock():
                    print("正在安裝更新…", flush=True)
                    try:
                        apply(root)
                        setup(root)
                    except Exception:
                        if rollback(root):
                            setup(root)
                        raise
                pending = True
            except Exception as error:
                message = "請先關閉同一安裝目錄的其他 Ownsei 視窗" if isinstance(error, Timeout) else str(error)
                (cache / "error.txt").write_text(message, encoding="utf-8")
                print(f"更新失敗：{message}", flush=True)
                pending = False
            opening = False
    finally:
        primary.release()


def serve(command, root, env, port, opening, pending=False):
    process = subprocess.Popen(command, cwd=root, env=env)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(f"後端提早結束（{process.returncode}）")
            try:
                with urllib.request.urlopen(url + ("api/update" if pending else ""), timeout=0.5) as response:
                    if pending and json.load(response)["current"] != json.loads(
                        (root / "release.json").read_text(encoding="utf-8")
                    )["version"]:
                        raise RuntimeError("新版啟動後版本不符")
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        else:
            raise RuntimeError("Server did not become ready")
        if pending:
            cache = root / ".runtime-envs/update"
            (cache / "backup/plan.json").unlink()
            shutil.rmtree(cache / "backup", ignore_errors=True)
            for name in ("package", "ready.json", "error.txt"):
                (cache / name).unlink(missing_ok=True)
        if opening and not webbrowser.open(url):
            print(f"瀏覽器未開啟，請手動前往 {url}", flush=True)
        return process.wait()
    except KeyboardInterrupt:
        # The terminal sends Ctrl+C to both processes. Let the backend finish
        # its shutdown before falling back to termination (forcible on Windows).
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return 130
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
