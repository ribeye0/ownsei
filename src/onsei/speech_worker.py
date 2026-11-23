"""speech.cpp protocol 4 bridge; checkpoint each native-timestamp request."""

from __future__ import annotations
import base64
import json
import math
import queue
import subprocess
import threading
import time
import sys
import wave
from pathlib import Path
from onsei import backend
from onsei.util import atomic_json as save
from onsei.processes import stop_process


class Worker:
    def __init__(self, model, device, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.log = (directory / "speech.log").open("a")
        self.started = time.monotonic()
        self.process = subprocess.Popen(
            [
                str(backend.binary("speech")),
                "worker",
                str(model),
                "--device",
                device,
                "--threads",
                "8",
                "--timestamps",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            bufsize=1,
            env=backend.environment("speech"),
        )
        self.messages = queue.Queue()

        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except BaseException as error:
                self.messages.put(error)
            finally:
                self.messages.put(EOFError("speech worker stdout closed"))

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.ready = self.receive()
            if self.ready.get("type") != "ready" or self.ready.get("protocol") != 4:
                raise RuntimeError(f"Unsupported worker handshake: {self.ready}")
            self.startup_seconds = time.monotonic() - self.started
            save(directory / "ready.json", self.ready)
        except BaseException:
            self.close()
            raise

    def receive(self):
        value = self.messages.get(timeout=180)
        if isinstance(value, BaseException):
            raise value
        if value.get("type") in ("error", "fatal"):
            raise RuntimeError(json.dumps(value, ensure_ascii=False))
        return value

    def send(self, data):
        self.process.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def transcribe(self, identity, pcm):
        for seq, offset in enumerate(range(0, len(pcm), 32000)):
            self.send(
                {
                    "type": "chunk",
                    "id": identity,
                    "seq": seq,
                    "pcm": base64.b64encode(pcm[offset : offset + 32000]).decode(),
                }
            )
        self.send({"type": "transcribe", "id": identity, "sample_rate": 16000, "timestamps": True})
        while True:
            message = self.receive()
            if message.get("id") != identity:
                raise RuntimeError("Worker response ID mismatch")
            if message["type"] == "end":
                if message.get("stop") != "complete":
                    raise RuntimeError(f"Incomplete recognition: {message.get('stop')}")
                return message
            if message["type"] != "progress":
                raise RuntimeError(f"Unexpected worker response: {message['type']}")

    def close(self):
        stop_process(self.process)
        self.log.close()


def timestamp_validation(result, duration):
    def plain(text):
        return "".join(c for c in text if c.isalnum())

    tokens, segments = result.get("tokens", []), result.get("segments", [])
    if result.get("text", "").strip() and (not tokens or not segments):
        raise ValueError("Nonempty recognition has no native timestamps")
    for kind, rows in [("tokens", tokens), ("segments", segments)]:
        last = 0.0
        for row in rows:
            start, end = row["start"], row["end"]
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, end)):
                raise ValueError(f"Non-finite {kind} interval")
            if start < -0.001 or end < start or end > duration + 0.081 or start < last - 0.001:
                raise ValueError(f"Invalid {kind} interval: {start}, {end}, audio={duration}")
            last = end
        if plain("".join(r["text"] for r in rows)) != plain(result.get("text", "")):
            raise ValueError(f"{kind} do not cover the recognized text")


def subtitle_segments(result):
    """Do not let the empty SentencePiece prefix at t=0 pull a subtitle earlier."""
    for segment in result.get("segments", []):
        spoken = [
            token
            for token in result.get("tokens", [])
            if any(c.isalnum() for c in token["text"])
            and token["end"] > token["start"]
            and token["end"] > segment["start"]
            and token["start"] < segment["end"]
        ]
        if not spoken or not segment["text"].strip():
            continue
        yield {
            **segment,
            "start": max(segment["start"], spoken[0]["start"]),
            "end": min(segment["end"], spoken[-1]["end"]),
        }


def native_words(result):
    """Keep zero-duration punctuation with its neighbouring native speech token."""
    words, prefix = [], ""
    for token in result.get("tokens", []):
        text = token["text"]
        if not text.strip():
            continue
        if token["end"] <= token["start"]:
            if words:
                words[-1]["text"] += text
            else:
                prefix += text
        else:
            words.append({"text": prefix + text, "start": token["start"], "end": token["end"]})
            prefix = ""
    return words


def run(request):
    checkpoint = Path(request["model"])
    model = checkpoint / request["filename"]
    device = request["device"]
    directory = Path(request["items"][0]["output"]).parent
    worker = Worker(model, device, directory)
    try:
        with wave.open(str(request["audio"])) as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise ValueError("speech.cpp input must be 16 kHz mono PCM16")
            for item in request["items"]:
                if Path(item["output"]).exists():
                    continue
                audio.setpos(round(item["start_ms"] * 16))
                pcm = audio.readframes(round((item["end_ms"] - item["start_ms"]) * 16))
                before = time.monotonic()
                result = worker.transcribe(str(item["index"]), pcm)
                timestamp_validation(result, len(pcm) / 32000)
                words = native_words(result)
                # Empty prefix tokens have no speech ownership and must not pull a cue to t=0.
                data = {
                    "text": result["text"],
                    "start": 0,
                    "end": len(pcm) / 32000,
                    "words": words,
                    "avg_logprob": None,
                }
                save(
                    Path(item["output"]),
                    {
                        "data": data,
                        "native": result,
                        "kind": "speech",
                        "chunk": item,
                        "device_requested": device,
                        "ready": worker.ready,
                        "seconds": time.monotonic() - before,
                    },
                )
                print(
                    json.dumps({"completed": item["output"], "seconds": time.monotonic() - before}),
                    flush=True,
                )
    finally:
        worker.close()


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()))
