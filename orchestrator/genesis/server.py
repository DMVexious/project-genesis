"""Phase 2: FastAPI + WebSocket orchestrator serving the Phaser front end.

    python -m genesis.server            # then open http://127.0.0.1:8765

The server starts on the start screen: a world exists but waits, paused, until
the player picks a map and presses Start (``--autostart`` skips that).

Client -> server messages (JSON): {"cmd": "pause" | "resume" | "step"},
{"cmd": "speed", "tick_ms": 300}, {"cmd": "reset", "seed": 123, "map": ..., "width": ...,
"height": ..., "population": ...} (a new world, which starts at once),
{"cmd": "models", "think": name, "talk": name, "best_of": n} (change the LLMs; any may be left
out, and with none it just re-reads what Ollama has installed),
{"cmd": "preview", ...same options...} (what that world would look like, answered with
{"type": "preview", ...} to the asker only), {"cmd": "world", "op": {...}} (see World.apply),
{"cmd": "power", "power": key, "x": .., "y": .., "npc": ..} (a god power: see powers.py),
{"cmd": "save" | "load" | "delete_save", "name": ...} and {"cmd": "saves"} (saved games: see saves.py).
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
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import hearing, saves
from .brain import installed_models
from .config import add_sim_args, build_sim, model_of, switch_models
from .engine import MAPS, World
from .engine.defs import client_defs
from .powers import MODES, client_powers

log = logging.getLogger("genesis.server")
# Browsers refuse ES modules served as text/plain, which is what Windows' registry can say .js is.
mimetypes.add_type("text/javascript", ".js")
WEB_DIR = Path(os.environ.get("GENESIS_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


# The sizes a world may be (tiles) and how many founders it may start with.
LIMITS = {"width": (24, 200), "height": (18, 150), "population": (1, 24)}
AUTOSAVE_TICKS = 1200   # ticks between autosaves (~4-5 minutes at the default speed)


class Hub:
    def __init__(self, sim, tick_ms: int, menu: bool = False, args=None):
        self.sim = sim
        self.args = args
        self.installed: list[str] = []  # Ollama's models (see _cmd_models)
        self.switching = False
        self.tick_ms = tick_ms
        # On the start screen the world waits: nothing moves until the player picks a map.
        self.menu = menu
        self.paused = menu
        self.clients: set[WebSocket] = set()
        self._step_once = asyncio.Event()
        self._lock = asyncio.Lock()
        self._sent = None  # (world, rev, trev) of the last frame broadcast
        self.save_dir = Path(getattr(args, "save_dir", None) or saves.DEFAULT_DIR)
        self.autosave_every = getattr(args, "autosave", AUTOSAVE_TICKS) if args is not None else 0
        self._saved_at = 0  # tick of the last autosave

    def control(self) -> dict:
        cfg = self.sim.cfg
        return {"type": "control", "paused": self.paused, "menu": self.menu, "tick_ms": self.tick_ms,
                "seed": cfg.seed, "map": cfg.map_name, "width": cfg.width, "height": cfg.height,
                "population": cfg.population, "god_mode": cfg.god_mode,
                "models": {"installed": self.installed, "think": model_of(self.sim.brain),
                           "talk": model_of(self.sim.talker), "best_of": getattr(self.sim.talker, "best_of", 1),
                           "switching": self.switching}}

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
                if self.autosave_every and frame["tick"] - self._saved_at >= self.autosave_every                         and frame["stats"]["population"] > 0:
                    self._saved_at = frame["tick"]
                    await self._save(saves.AUTOSAVE)
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
        if msg.get("mode") in MODES:
            cfg.god_mode = msg["mode"]
        self._saved_at = 0
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

    async def _cmd_models(self, msg, ws):
        """Change which LLMs villagers think and talk with (see config.switch_models)."""
        if self.args is None or self.switching:
            return False
        want = {k: msg.get(k) or None for k in ("think", "talk", "best_of")}
        if any(want.values()):
            self.switching = True
            await self.broadcast(self.control())
            try:
                await switch_models(self.sim, self.args, want["think"], want["talk"], want["best_of"])
            finally:
                self.switching = False
        self.installed = await installed_models(self.args.ollama_url)

    # --- the player: becoming someone, telling them what to do, speaking, god powers ---
    async def _cmd_possess(self, msg, ws):
        """{"cmd": "possess", "npc": id}: become that villager."""
        if not self.sim.possess(int(msg.get("npc", -1))):
            return False

    async def _cmd_release(self, msg, ws):
        """Leave the body; it goes back to living its own life."""
        self.sim.release()

    async def _cmd_let_go(self, msg, ws):
        """After the player's body died: carry on as nobody (just watch)."""
        self.sim.let_go()

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

    async def _cmd_power(self, msg, ws):
        """{"cmd": "power", "power": key, "x": .., "y": .. | "npc": id}: a god power (see powers.py).
        The asker is told how it went if it failed or there is something to say."""
        async with self._lock:
            ok, text = self.sim.power(str(msg.get("power", "")), int(msg.get("x", -1)), int(msg.get("y", -1)),
                                      int(msg.get("npc", -1)))
        if ws is not None and (text or not ok):
            await ws.send_json({"type": "toast", "ok": ok, "text": text, "power": msg.get("power")})
        return False

    # --- saved games -------------------------------------------------------------
    async def _save(self, name: str) -> str | None:
        """Save the game; returns the error, if it failed."""
        async with self._lock:  # (between ticks: the world holds still while it is packed)
            info, blob = saves.pack(self.sim)
        try:
            await asyncio.get_running_loop().run_in_executor(None, saves.write, self.save_dir, name, info, blob)
        except OSError as e:
            log.warning("saving %r failed: %s", name, e)
            return str(e)
        log.info("saved %r (%d KB)", name, len(blob) // 1024)
        return None

    async def _cmd_save(self, msg, ws):
        name = saves.clean_name(msg.get("name") or "save")
        err = await self._save(name)
        if ws is not None:
            await ws.send_json({"type": "toast", "ok": err is None,
                                "text": f"Couldn't save: {err}" if err else f"Saved as \"{name}\""})
            await self._cmd_saves(msg, ws)
        return False

    async def _cmd_saves(self, msg, ws):
        """{"cmd": "saves"}: the list of saved games, to the asker only."""
        if ws is not None:
            listed = await asyncio.get_running_loop().run_in_executor(None, saves.listing, self.save_dir)
            await ws.send_json({"type": "saves", "saves": listed})
        return False

    async def _cmd_delete_save(self, msg, ws):
        saves.delete(self.save_dir, msg.get("name", ""))
        await self._cmd_saves(msg, ws)
        return False

    async def _cmd_load(self, msg, ws):
        """{"cmd": "load", "name": ...}: carry on a saved game (it starts paused)."""
        name = msg.get("name", "")
        try:
            snap = await asyncio.get_running_loop().run_in_executor(None, saves.read, self.save_dir, name)
        except (OSError, ValueError, EOFError) as e:
            if ws is not None:
                await ws.send_json({"type": "toast", "ok": False, "text": f"Couldn't load that save: {e}"})
            return False
        async with self._lock:
            self.sim.restore(snap)
        self.paused, self.menu = True, False
        self._sent = None
        self._saved_at = self.sim.state["tick"]
        await self.broadcast(self.sim.last_frame)
        if ws is not None:
            await ws.send_json({"type": "toast", "ok": True,
                                "text": f"Loaded \"{saves.clean_name(name)}\". Press play (or Space) to carry on."})

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
        hub = Hub(sim, args.tick_ms, menu=not args.autostart, args=args)
        hub.installed = await installed_models(args.ollama_url)
        hub.paused = hub.paused or args.paused
        resume = getattr(args, "resume", None)
        if resume:  # carry on a saved world (the autosave by default) instead of the start screen
            if saves.exists(hub.save_dir, resume):
                await hub._cmd_load({"name": resume}, None)
                hub.paused = bool(getattr(args, "paused", False))
                log.info("resumed %r at tick %d", resume, sim.state["tick"])
            else:
                log.warning("no save called %r in %s: starting afresh", resume, hub.save_dir)
        app.state.hub = hub
        task = asyncio.create_task(hub.run())
        yield
        task.cancel()
        sim.engine.close()
        for b in {id(sim.brain): sim.brain, id(sim.talker): sim.talker}.values():
            if hasattr(b, "close"):
                await b.close()

    app = FastAPI(title="Project Genesis", lifespan=lifespan)

    @app.get("/api/frame")
    async def frame():
        return app.state.hub.sim.last_frame

    # The player's voice: a recorded clip in, its words out (see hearing.py).
    @app.get("/api/hear")
    async def can_hear():
        return {"available": hearing.available()}

    @app.post("/api/hear")
    async def hear(request: Request):
        if not hearing.available():
            raise HTTPException(501, "speech recognition is not installed")
        audio = await request.body()
        if len(audio) > hearing.MAX_BYTES:
            raise HTTPException(413, "clip too long")
        names = [n["name"] for n in app.state.hub.sim.last_frame.get("npcs", []) if n.get("alive")]
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, hearing.transcribe, audio, names)
        except Exception as e:  # (a clip it can't decode, say)
            log.warning("could not hear that clip: %s", e)
            raise HTTPException(400, "could not make that out")
        return {"text": text}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        hub: Hub = app.state.hub
        await ws.accept()
        hub.clients.add(ws)
        await ws.send_json({"type": "defs", **client_defs(), "limits_world": LIMITS, "powers": client_powers(),
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
        app.mount("/", _FreshStaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


class _FreshStaticFiles(StaticFiles):
    """Static files the browser always revalidates (cheap 304s via the etag): without
    this a refresh can keep running yesterday's JS modules after the page changed."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


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
    p.add_argument("--save-dir", default=None, help=f"where saved games go (default: {saves.DEFAULT_DIR})")
    p.add_argument("--autosave", type=int, default=AUTOSAVE_TICKS, help="ticks between autosaves (0: never)")
    p.add_argument("--resume", nargs="?", const=saves.AUTOSAVE, default=None, metavar="SAVE",
                   help="carry on a saved world at once (default: the autosave), memories and all")
    args = p.parse_args()
    if args.parent_pid:
        watch_parent(args.parent_pid)
    uvicorn.run(create_app(args), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
