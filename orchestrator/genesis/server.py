"""Phase 2: FastAPI + WebSocket orchestrator serving the Phaser front end.

    python -m genesis.server            # then open http://127.0.0.1:8765

Client -> server messages (JSON): {"cmd": "pause" | "resume" | "step"},
{"cmd": "speed", "tick_ms": 300}, {"cmd": "reset", "seed": 123, "map": ..., "width": ...,
"height": ..., "population": ...}, {"cmd": "world", "op": {...}} (see World.apply).
Server -> client: {"type": "defs", ...} once on connect (the content registry),
{"type": "frame", ...} every tick (see sim.Simulation._frame), plus
{"type": "control", "paused": bool, "tick_ms": int, ...} on changes.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import mimetypes
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import add_sim_args, build_sim
from .engine import MAPS
from .engine.defs import client_defs

log = logging.getLogger("genesis.server")
# Browsers refuse ES modules served as text/plain, which is what Windows' registry can say .js is.
mimetypes.add_type("text/javascript", ".js")
WEB_DIR = Path(os.environ.get("GENESIS_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


class Hub:
    def __init__(self, sim, tick_ms: int):
        self.sim = sim
        self.tick_ms = tick_ms
        self.paused = False
        self.clients: set[WebSocket] = set()
        self._step_once = asyncio.Event()
        self._lock = asyncio.Lock()
        self._sent_map = None  # (width, height, rev) of the last map broadcast

    def control(self) -> dict:
        cfg = self.sim.cfg
        return {"type": "control", "paused": self.paused, "tick_ms": self.tick_ms, "seed": cfg.seed,
                "map": cfg.map_name, "width": cfg.width, "height": cfg.height, "population": cfg.population}

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run(self):
        while True:
            t0 = time.perf_counter()
            if not self.paused or self._step_once.is_set():
                self._step_once.clear()
                async with self._lock:
                    try:
                        frame = await self.sim.tick()
                    except Exception:
                        log.exception("tick failed; pausing")
                        self.paused = True
                        await self.broadcast(self.control())
                        continue
                # The map is most of a frame and changes on few ticks: send it only when it did.
                # (A client that just connected was sent the whole latest frame; see ws_endpoint.)
                key = (frame["width"], frame["height"], frame["rev"])
                if key == self._sent_map:
                    frame = {k: v for k, v in frame.items() if k != "tiles"}
                self._sent_map = key
                await self.broadcast(frame)
                if frame["stats"]["population"] == 0 and not self.paused:
                    self.paused = True
                    await self.broadcast(self.control())
            await asyncio.sleep(max(0.02, self.tick_ms / 1000 - (time.perf_counter() - t0)))

    # --- client commands: {"cmd": name, ...} -> _cmd_<name> ---------------------
    async def handle(self, msg: dict):
        fn = getattr(self, f"_cmd_{msg.get('cmd')}", None)
        if fn is None:
            log.warning("unknown client command %r", msg.get("cmd"))
            return
        await fn(msg)
        await self.broadcast(self.control())

    async def _cmd_pause(self, msg):
        self.paused = True

    async def _cmd_resume(self, msg):
        self.paused = False

    async def _cmd_step(self, msg):
        self.paused = True
        self._step_once.set()

    async def _cmd_speed(self, msg):
        self.tick_ms = max(50, min(2000, int(msg.get("tick_ms", self.tick_ms))))

    async def _cmd_reset(self, msg):
        """New world. Optional: seed, map, width, height, population."""
        cfg = self.sim.cfg
        seed = msg.get("seed")
        if msg.get("map") in MAPS:
            cfg.map_name = msg["map"]
        for key, lo, hi in (("width", 16, 240), ("height", 12, 180), ("population", 1, 60)):
            if msg.get(key) is not None:
                setattr(cfg, key, max(lo, min(hi, int(msg[key]))))
        async with self._lock:
            self.sim.reset(int(seed) if seed is not None else None)
        self._sent_map = None
        await self.broadcast(self.sim.last_frame)

    async def _cmd_world(self, msg):
        """Change the world directly (see ``World.apply``); applied at the start of the next tick."""
        op = msg.get("op")
        if isinstance(op, dict):
            self.sim.enqueue(op)


def create_app(args) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        sim = await build_sim(args)
        hub = Hub(sim, args.tick_ms)
        hub.paused = args.paused
        app.state.hub = hub
        task = asyncio.create_task(hub.run())
        yield
        task.cancel()
        sim.engine.close()
        if hasattr(sim.brain, "close"):
            await sim.brain.close()

    app = FastAPI(title="Project Genesis", lifespan=lifespan)

    @app.get("/api/frame")
    async def frame():
        return app.state.hub.sim.last_frame

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        hub: Hub = app.state.hub
        await ws.accept()
        hub.clients.add(ws)
        await ws.send_json({"type": "defs", **client_defs(), "maps": sorted(MAPS)})
        await ws.send_json(hub.control())
        await ws.send_json(hub.sim.last_frame)
        try:
            while True:
                msg = await ws.receive_json()
                try:
                    await hub.handle(msg if isinstance(msg, dict) else {})
                except (TypeError, ValueError) as e:  # e.g. {"cmd": "reset", "seed": "abc"}
                    log.warning("ignoring bad client message %r: %s", msg, e)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(ws)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watch_parent(pid: int):
    """Exit when the launching app (the Tauri shell) dies, so the sidecar
    never outlives the window - even if the app was killed without cleanup."""
    def loop():
        while _pid_alive(pid):
            time.sleep(1)
        log.info("parent process %d exited; shutting down", pid)
        os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_sim_args(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--tick-ms", type=int, default=150)
    p.add_argument("--paused", action="store_true", help="start paused")
    p.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits (sidecar mode)")
    args = p.parse_args()
    if args.parent_pid:
        watch_parent(args.parent_pid)
    uvicorn.run(create_app(args), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
