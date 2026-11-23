"""Local UI launch must tolerate a different installation already running."""

import importlib
from pathlib import Path
import socket
import sys

import pytest


@pytest.fixture
def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("launch")


def test_default_port_skips_an_existing_listener(launcher, monkeypatch):
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        occupied = existing.getsockname()[1]
        monkeypatch.setattr(launcher, "DEFAULT_PORT", occupied)
        selected = launcher.select_port()
        assert selected != occupied
        with socket.socket() as available:
            available.bind(("127.0.0.1", selected))


def test_explicit_occupied_port_is_not_silently_changed(launcher):
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        with pytest.raises(OSError):
            launcher.select_port(existing.getsockname()[1])


def test_explicit_available_port_is_preserved(launcher):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    assert launcher.select_port(port) == port


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX listener reuse")
def test_recently_closed_http_connection_does_not_block_restart(launcher):
    with socket.socket() as server, socket.socket() as client:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen()
        client.connect(("127.0.0.1", port))
        connection, _ = server.accept()
        connection.close()
        assert client.recv(1) == b""
    assert launcher.select_port(port) == port
