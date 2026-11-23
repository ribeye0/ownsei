"""Run the local UI and open a browser after the server is ready."""

import argparse
import errno
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

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
    process = subprocess.Popen(
        [sys.executable, "-m", "onsei.cli", "serve", "--port", str(port)], cwd=root, env=env
    )
    url = f"http://127.0.0.1:{port}/"
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
        print(f"Ownsei: {url}", flush=True)
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
