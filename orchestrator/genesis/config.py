"""Shared command-line options for the terminal runner and the server."""
from __future__ import annotations

import argparse
import logging
import os

from .brain import make_brain
from .engine import GAME_MAP, MAPS, Engine
from .memory import make_memory
from .sim import SimConfig, Simulation


def add_sim_args(p: argparse.ArgumentParser):
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--width", type=int, default=80)
    p.add_argument("--height", type=int, default=56)
    p.add_argument("--population", type=int, default=3, help="founders (they start together)")
    p.add_argument("--map", choices=list(MAPS), default=GAME_MAP,
                   help="the kind of land: " + ", ".join(f"{m.key} ({m.label})" for m in MAPS.values()))
    p.add_argument("--brain", choices=["auto", "llm", "ollama", "heuristic"], default="auto",
                   help="auto = llama.cpp if its server answers, else Ollama, else heuristic")
    p.add_argument("--llm-url", default="http://127.0.0.1:8080", help="llama-server base URL")
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434"))
    p.add_argument("--model", default=os.environ.get("GENESIS_MODEL", "bonsai-2:2b"),
                   help="Ollama model name (env GENESIS_MODEL)")
    p.add_argument("--llm-every", type=int, default=18,
                   help="ticks between each NPC's LLM goal check; social moments always use the LLM")
    p.add_argument("--llm-budget", type=int, default=2, help="max brain requests in flight at once")
    p.add_argument("--memory", choices=["auto", "chromadb", "simple"], default="auto")
    p.add_argument("--memory-path", default=None, help="persist ChromaDB here (default: in-memory)")
    p.add_argument("-v", "--verbose", action="store_true")


async def build_sim(args) -> Simulation:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    engine = Engine()
    brain = await make_brain(args.brain, args.llm_url, args.seed, args.ollama_url, args.model)
    memory = make_memory(args.memory, args.memory_path)
    cfg = SimConfig(seed=args.seed, width=args.width, height=args.height, population=args.population,
                    llm_interval=args.llm_every, llm_budget=args.llm_budget, map_name=args.map)
    logging.getLogger("genesis").info("map=%s brain=%s memory=%s", cfg.map_name, brain.name, memory.name)
    return Simulation(engine, brain, memory, cfg)
