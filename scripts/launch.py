"""Run the local UI and open a browser after the server is ready."""

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8878)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error("Port already in use; choose another with --port")
    env = {**os.environ, "PYTHONUTF8": "1", "ONSEI_ROOT": str(root)}
    process = subprocess.Popen(
        [sys.executable, "-m", "onsei.cli", "serve", "--port", str(args.port)], cwd=root, env=env
    )
    url = f"http://127.0.0.1:{args.port}/"
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise SystemExit(process.returncode or 1)
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        else:
            raise RuntimeError("Server did not become ready")
        if not args.no_browser:
            webbrowser.open(url)
        raise SystemExit(process.wait())
    except KeyboardInterrupt:
        pass
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
