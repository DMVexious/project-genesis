"""Saved games: a world, its villagers' minds and memories, kept on disk.

A save file holds a one-line JSON header (what the save list shows: when, which
land, what day, how many people) and then the game itself, pickled and
gzipped. Saves live in one folder (``~/.genesis/saves`` unless told
otherwise) and are only ever read from there by name. Pickle runs code from
the file it loads, so only load saves made by this game on this machine:
never a save file someone sent you.
"""
from __future__ import annotations

import gzip
import json
import os
import pickle
import re
import time
from pathlib import Path

VERSION = 1
SUFFIX = ".genesis"
AUTOSAVE = "autosave"
DEFAULT_DIR = Path.home() / ".genesis" / "saves"


def clean_name(name: str) -> str:
    """A save's name as a safe file name: letters, digits, spaces, - and _ only."""
    name = re.sub(r"[^A-Za-z0-9 _-]", "", str(name)).strip()[:40]
    return name or "save"


def info_of(sim) -> dict:
    alive = [n for n in sim.state["npcs"] if n["alive"]]
    clock = sim.state.get("clock", {})
    return {"version": VERSION, "saved": time.time(), "map": sim.cfg.map_name, "seed": sim.cfg.seed,
            "width": sim.cfg.width, "height": sim.cfg.height, "tick": sim.state["tick"],
            "day": clock.get("day"), "season": clock.get("season"), "people": len(alive),
            "villages": [sim.village_name(v["id"]) for v in sim.state.get("villages", []) if not v["abandoned"]],
            "mode": sim.cfg.god_mode}


def pack(sim) -> tuple[dict, bytes]:
    """Take the game as it stands (call between ticks: the world must not change meanwhile)."""
    return info_of(sim), pickle.dumps(sim.snapshot(), protocol=pickle.HIGHEST_PROTOCOL)


def write(folder: Path, name: str, info: dict, blob: bytes) -> Path:
    """Write a packed game to disk (slow: run it off the event loop)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (clean_name(name) + SUFFIX)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps({**info, "name": clean_name(name)}).encode() + b"\n")
        f.write(gzip.compress(blob, compresslevel=5))
    os.replace(tmp, path)  # (a crash mid-write never leaves half a save)
    return path


def read(folder: Path, name: str) -> dict:
    """The snapshot in a save (see ``Simulation.restore``)."""
    path = folder / (clean_name(name) + SUFFIX)
    with open(path, "rb") as f:
        info = json.loads(f.readline())
        if info.get("version") != VERSION:
            raise ValueError(f"save {name!r} is from another version of the game")
        return pickle.loads(gzip.decompress(f.read()))


def listing(folder: Path) -> list[dict]:
    """Every save in the folder, newest first."""
    out = []
    for path in folder.glob("*" + SUFFIX) if folder.is_dir() else []:
        try:
            with open(path, "rb") as f:
                info = json.loads(f.readline())
            out.append({**info, "name": path.stem, "bytes": path.stat().st_size})
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: -s.get("saved", 0))


def delete(folder: Path, name: str) -> bool:
    path = folder / (clean_name(name) + SUFFIX)
    if path.is_file():
        path.unlink()
        return True
    return False
