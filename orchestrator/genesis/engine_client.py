"""Clients for the game engine.

Both clients speak the same line protocol (see engine_py.py). ``BinaryEngine``
drives the compiled C++ engine as a child process (this is what the Tauri
sidecar setup uses); ``PyEngine`` runs the reference engine in-process so the
simulation works before the C++ binary has been built.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .engine_py import EngineProcess

ROOT = Path(__file__).resolve().parents[2]
EXE_NAME = "engine.exe" if sys.platform == "win32" else "engine"


class EngineError(RuntimeError):
    pass


def _encode_choices(choices: dict[int, tuple[int, int]]) -> str:
    return " ".join(f"{nid}:{idx}:{opt}" for nid, (idx, opt) in sorted(choices.items()))


def _decode(line: str) -> dict:
    data = json.loads(line)
    if "error" in data:
        raise EngineError(data["error"])
    return data


class PyEngine:
    name = "python"

    def __init__(self):
        self._proc = EngineProcess()

    def init(self, seed: int, width: int, height: int, population: int, decay_every: int = 1) -> dict:
        return _decode(self._proc.handle(f"INIT {seed} {width} {height} {population} {decay_every}"))

    def step(self, choices: dict[int, tuple[int, int]]) -> dict:
        return _decode(self._proc.handle(f"STEP {_encode_choices(choices)}"))

    def close(self):
        pass


class BinaryEngine:
    name = "c++"

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._p = subprocess.Popen(
            [str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=flags,
        )

    def _send(self, line: str) -> dict:
        if self._p.poll() is not None:
            raise EngineError(f"engine exited with code {self._p.returncode}: {self._p.stderr.read()}")
        self._p.stdin.write(line + "\n")
        self._p.stdin.flush()
        out = self._p.stdout.readline()
        if not out:
            raise EngineError(f"engine closed its output: {self._p.stderr.read()}")
        return _decode(out)

    def init(self, seed: int, width: int, height: int, population: int, decay_every: int = 1) -> dict:
        return self._send(f"INIT {seed} {width} {height} {population} {decay_every}")

    def step(self, choices: dict[int, tuple[int, int]]) -> dict:
        return self._send(f"STEP {_encode_choices(choices)}")

    def close(self):
        if self._p.poll() is None:
            try:
                self._p.stdin.write("QUIT\n")
                self._p.stdin.flush()
                self._p.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._p.kill()
                self._p.wait()
        for pipe in (self._p.stdin, self._p.stdout, self._p.stderr):
            try:
                pipe.close()
            except OSError:
                pass


def find_engine_binary() -> Path | None:
    """Locate the compiled engine: $GENESIS_ENGINE, next to a frozen exe, or engine/build."""
    candidates = []
    if os.environ.get("GENESIS_ENGINE"):
        candidates.append(Path(os.environ["GENESIS_ENGINE"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / EXE_NAME)
    candidates.append(ROOT / "engine" / "build" / EXE_NAME)
    for c in candidates:
        if c.is_file():
            return c
    return None


def make_engine(kind: str = "auto"):
    """kind: 'auto' (C++ if built, else Python), 'cpp', or 'python'."""
    if kind == "python":
        return PyEngine()
    path = find_engine_binary()
    if path is None:
        if kind == "cpp":
            raise EngineError(f"C++ engine not found; build it (see README) or set GENESIS_ENGINE")
        return PyEngine()
    return BinaryEngine(path)
