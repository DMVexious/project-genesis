"""Phase 2: FastAPI + WebSocket orchestrator serving the Phaser front end.

    python -m genesis.server            # then open http://127.0.0.1:8765

The server starts on the start screen: a world exists but waits, paused, until
the player picks a map and presses Start (``--autostart`` skips that).

Client -> server messages (JSON): {"cmd": "pause" | "resume" | "step"},
{"cmd": "speed", "tick_ms": 300}, {"cmd": "reset", "seed": 123, "map": ..., "width": ...,
"height": ..., "population": ...} (a new world, which starts at once),
{"cmd": "preview", ...same options...} (what that world would look like, answered with
{"type": "preview", ...} to the asker only), {"cmd": "world", "op": {...}} (see World.apply).
Server -> client: {"type": "defs", ...} once on connect (the content registry and the maps),
{"type": "frame", ...} every tick (see sim.Simulation._frame), plus
{"type": "control", "paused": bool, "menu": bool, "tick_ms": int, ...} on changes.
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
from .engine import MAPS, World
from .engine.defs import client_defs

log = logging.getLogger("genesis.server")
# Browsers refuse ES modules served as text/plain, which is what Windows' registry can say .js is.
mimetypes.add_type("text/javascript", ".js")
WEB_DIR = Path(os.environ.get("GENESIS_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


# The sizes a world may be (tiles) and how many founders it may start with.
LIMITS = {"width": (24, 200), "height": (18, 150), "population": (1, 24)}


class Hub:
    def __init__(self, sim, tick_ms: int, menu: bool = False):
        self.sim = sim
        self.tick_ms = tick_ms
        # On the start screen the world waits: nothing moves until the player picks a map.
        self.menu = menu
        self.paused = menu
        self.clients: set[WebSocket] = set()
        self._step_once = asyncio.Event()
        self._lock = asyncio.Lock()
        self._sent = None  # (world, rev, trev) of the last frame broadcast

    def control(self) -> dict:
        cfg = self.sim.cfg
        return {"type": "control", "paused": self.paused, "menu": self.menu, "tick_ms": self.tick_ms,
                "seed": cfg.seed, "map": cfg.map_name, "width": cfg.width, "height": cfg.height,
                "population": cfg.population}

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def _slim(self, frame: dict) -> dict:
        """The map is most of a frame and changes on few ticks: leave out the
        parts every client already has. (A client that just connected was sent
        the whole latest frame; see ws_endpoint.)"""
        key = (frame["world"], frame["rev"], frame["trev"])
        last, self._sent = self._sent, key
        if last is None or last[0] != key[0]:
            return frame
        have = {name for name, i in (("tiles", 1), ("terrain", 2)) if last[i] == key[i]}
        return {k: v for k, v in frame.items() if k not in have} if have else frame

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
                await self.broadcast(self._slim(frame))
                if frame["stats"]["population"] == 0 and not self.paused:
                    self.paused = True
                    await self.broadcast(self.control())
            await asyncio.sleep(max(0.02, self.tick_ms / 1000 - (time.perf_counter() - t0)))

    # --- client commands: {"cmd": name, ...} -> _cmd_<name>(msg, ws) -------------
    async def handle(self, msg: dict, ws: WebSocket | None = None):
        fn = getattr(self, f"_cmd_{msg.get('cmd')}", None)
        if fn is None:
            log.warning("unknown client command %r", msg.get("cmd"))
            return
        if await fn(msg, ws) is not False:  # (False: nothing changed that the others need to hear about)
            await self.broadcast(self.control())

    async def _cmd_pause(self, msg, ws):
        self.paused = True

    async def _cmd_resume(self, msg, ws):
        self.paused = self.menu = False

    async def _cmd_step(self, msg, ws):
        self.paused = True
        self._step_once.set()

    async def _cmd_speed(self, msg, ws):
        self.tick_ms = max(50, min(2000, int(msg.get("tick_ms", self.tick_ms))))

    def _world_options(self, msg) -> dict:
        """The world a client asked for: its own choices where given (and
        sensible), the current settings otherwise."""
        cfg = self.sim.cfg
        want = {"seed": int(msg["seed"]) if msg.get("seed") is not None else cfg.seed,
                "map": msg["map"] if msg.get("map") in MAPS else cfg.map_name}
        for key, (lo, hi) in LIMITS.items():
            want[key] = max(lo, min(hi, int(msg[key]))) if msg.get(key) is not None else getattr(cfg, key)
        return want

    async def _cmd_reset(self, msg, ws):
        """A new world, which starts at once. Optional: seed, map, width, height, population."""
        cfg = self.sim.cfg
        want = self._world_options(msg)
        cfg.map_name, cfg.width, cfg.height, cfg.population = (want[k] for k in ("map", "width", "height",
                                                                                 "population"))
        async with self._lock:
            self.sim.reset(want["seed"])
        self.paused = self.menu = False
        self._sent = None
        await self.broadcast(self._slim(self.sim.last_frame))

    async def _cmd_preview(self, msg, ws):
        """What a world with these options would look like (the very map a reset
        with the same options makes). Answers only the client that asked."""
        want = self._world_options(msg)
        make = lambda: World(want["seed"], want["width"], want["height"], want["population"],  # noqa: E731
                             self.sim.cfg.decay_every, want["map"])
        world = await asyncio.get_running_loop().run_in_executor(None, make)  # (off the tick loop: it takes a while)
        if ws is not None:
            await ws.send_json({"type": "preview", **want, "terrain": world.terrain_rows(), "tiles": world.rows(),
                                "start": list(world.start), "id": msg.get("id")})
        return False

    # --- the player: becoming someone, telling them what to do, speaking, god powers ---
    async def _cmd_possess(self, msg, ws):
        """{"cmd": "possess", "npc": id}: become that villager."""
        if not self.sim.possess(int(msg.get("npc", -1))):
            return False

    async def _cmd_release(self, msg, ws):
        """Leave the body; it goes back to living its own life."""
        self.sim.release()

    async def _cmd_player(self, msg, ws):
        """{"cmd": "player", "order": {...}}: tell the body what to do (see Simulation.command)."""
        self.sim.command(msg.get("order"))
        return False

    async def _cmd_chat(self, msg, ws):
        """{"cmd": "chat", "text": "..."}: say something in the conversation you're in."""
        self.sim.chat_say(str(msg.get("text", "")))
        return False

    async def _cmd_chat_end(self, msg, ws):
        """End the conversation you're in."""
        self.sim.end_chat()
        return False

    async def _cmd_answer(self, msg, ws):
        """{"cmd": "answer", "text": "...", "accept": bool}: answer whoever is speaking to your body."""
        self.sim.answer(str(msg.get("text", "")), bool(msg.get("accept", False)))
        return False

    async def _cmd_say(self, msg, ws):
        """{"cmd": "say", "npc": id, "text": "..."}: speak to a villager from the sky; they answer."""
        self.sim.say(int(msg.get("npc", -1)), str(msg.get("text", "")))
        return False

    async def _cmd_god(self, msg, ws):
        """{"cmd": "god", "power": "heal" | "feed" | "smite", "npc": id}."""
        self.sim.god(str(msg.get("power", "")), int(msg.get("npc", -1)))
        return False

    async def _cmd_world(self, msg, ws):
        """Change the world directly (see ``World.apply``); applied at the start of the next tick."""
        op = msg.get("op")
        if isinstance(op, dict):
            self.sim.enqueue(op)
        return False


def create_app(args) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        sim = await build_sim(args)
        hub = Hub(sim, args.tick_ms, menu=not args.autostart)
        hub.paused = hub.paused or args.paused
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
        await ws.send_json({"type": "defs", **client_defs(), "limits_world": LIMITS,
                            "maps": [{"key": m.key, "label": m.label, "blurb": m.blurb} for m in MAPS.values()]})
        await ws.send_json(hub.control())
        await ws.send_json(hub.sim.last_frame)
        try:
            while True:
                msg = await ws.receive_json()
                try:
                    await hub.handle(msg if isinstance(msg, dict) else {}, ws)
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
    p.add_argument("--tick-ms", type=int, default=220)
    p.add_argument("--paused", action="store_true", help="start paused")
    p.add_argument("--autostart", action="store_true",
                   help="skip the start screen: begin at once on the world the options describe")
    p.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits (sidecar mode)")
    args = p.parse_args()
    if args.parent_pid:
        watch_parent(args.parent_pid)
    uvicorn.run(create_app(args), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
