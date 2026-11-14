"""Shared command-line options for the terminal runner and the server."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .brain import BEST_OF, HeuristicBrain, OllamaBrain, make_brain, make_talker
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
                   help="Ollama model villagers think with (env GENESIS_MODEL)")
    p.add_argument("--talk-model", default=os.environ.get("GENESIS_TALK_MODEL", "auto"),
                   help="Ollama model for what the player reads: conversations, the voice from the sky "
                        "(env GENESIS_TALK_MODEL). auto = the best installed of brain.TALK_MODELS; "
                        "same = the --model one")
    p.add_argument("--best-of", type=int, default=BEST_OF,
                   help="lines the talk model writes at once for each one it says (the best is kept)")
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
    if args.brain == "heuristic" and args.talk_model == "auto":
        args.talk_model = "heuristic"  # (asked for no LLM: none for talking either, unless named)
    talker = await make_talker(brain, args.talk_model, args.ollama_url, args.best_of)
    logging.getLogger("genesis").info("map=%s brain=%s talker=%s memory=%s", cfg.map_name, brain.name,
                                      talker.name, memory.name)
    return Simulation(engine, brain, memory, cfg, talker)


def model_of(brain) -> str:
    """Which model a brain runs: an Ollama name, "llama.cpp" or "heuristic"."""
    return getattr(brain, "model", None) or ("heuristic" if isinstance(brain, HeuristicBrain) else "llama.cpp")


async def _close_later(brain, delay: float = 60.0):
    """Close a brain no longer in use once calls already sent to it are done."""
    await asyncio.sleep(delay)
    if hasattr(brain, "close"):
        await brain.close()


async def switch_models(sim: Simulation, args, think: str | None = None, talk: str | None = None,
                        best_of: int | None = None):
    """Change the models while the world runs. ``think``: an Ollama model or "heuristic";
    ``talk``: as --talk-model. Anything left None stays as it is."""
    log = logging.getLogger("genesis")
    old_brain, old_talker = sim.brain, sim.talker
    if think and think != model_of(sim.brain):
        if think == "heuristic":
            sim.brain = sim.reflex
        else:
            brain = OllamaBrain(args.ollama_url, think, sim.reflex)
            try:
                await brain.warm_up()
            except Exception as e:
                log.warning("Could not load %s: %r", think, e)
                await brain.close()
                return
            sim.brain = brain
            args.model = think
        log.info("villagers now think with %s", sim.brain.name)
    if best_of:
        args.best_of = max(1, min(8, int(best_of)))
    if talk or best_of or sim.brain is not old_brain:
        args.talk_model = talk or args.talk_model
        sim.talker = await make_talker(sim.brain, args.talk_model, args.ollama_url, args.best_of)
        log.info("villagers now talk to you with %s", sim.talker.name)
    for b in {id(old_brain): old_brain, id(old_talker): old_talker}.values():
        if b not in (sim.brain, sim.talker, sim.reflex):
            asyncio.ensure_future(_close_later(b))
