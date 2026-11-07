"""Phase 1: run the simulation in the terminal.

    python -m genesis.terminal                   # live ASCII view
    python -m genesis.terminal --ticks 500 --headless
    python -m genesis.terminal --show-prompt     # print one NPC's prompt + grammar and exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from .brain import action_grammar, action_prompt, interaction_grammar
from .config import add_sim_args, build_sim

TILE_GLYPH = {".": ("·", "33"), "#": ("▲", "90"), "T": ("♣", "32"), "s": ("‚", "33"),
              "B": ("✿", "35"), "b": ("✽", "90"), "H": ("⌂", "93")}
NPC_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def render(frame: dict, recent: list[str]) -> str:
    grid = [[f"\x1b[{c}m{g}\x1b[0m" for g, c in (TILE_GLYPH[t] for t in row)] for row in frame["tiles"]]
    for n in frame["npcs"]:
        ch = NPC_CHARS[n["id"] % len(NPC_CHARS)]
        color = "31" if not n["alive"] else "97;1"
        grid[n["y"]][n["x"]] = f"\x1b[{color}m{ch}\x1b[0m"
    s = frame["stats"]
    out = [f"Genesis  tick {frame['tick']}  pop {s['population']}  births {s['births']}  deaths {s['deaths']}  "
           f"huts {s['huts']}  fights {s['fights']}  trades {s['trades']}  gen {s['max_generation']}  "
           f"[{frame['engine']}/{frame['brain']}/{frame['memory']}]"]
    out += ["".join(r) for r in grid]
    out.append("")
    for n in sorted(frame["npcs"], key=lambda n: n["id"]):
        ch = NPC_CHARS[n["id"] % len(NPC_CHARS)]
        t = n["traits"]
        out.append(f" {ch} {n['name']:<10} hp{n['health']:>3} fd{n['hunger']:>3} en{n['energy']:>3} "
                   f"so{n['social']:>3} w{n['wood']:>2} f{n['food']:>2}{'⚔' if n['spear'] else ' '} "
                   f"{n['status'] or ' '}{n['emoji'] or ' '} {t['aggression'][:5]}/{t['work'][:5]}/{t['social'][:5]}"
                   f"  {n['intent']}")
    for d in frame["dialogue"]:
        recent.append(f'💬 {next((n["name"] for n in frame["npcs"] if n["id"] == d["npc"]), "?")}: "{d["text"]}"')
    recent.extend(frame["log"])
    del recent[:-10]
    out.append("")
    out += recent
    return "\n".join(out)


async def show_prompt(args):
    sim = await build_sim(args)
    v = sim.view(sim.alive()[0])
    print("=== Action prompt ===")
    print(json.dumps(action_prompt(v, sim.names()), indent=2, ensure_ascii=False))
    print("=== Action grammar (GBNF) ===")
    print(action_grammar(len(v.actions)))
    print("=== Interaction grammar example ===")
    print(interaction_grammar(["talk", "fight", "trade"]))


async def run(args):
    if args.show_prompt:
        return await show_prompt(args)
    sim = await build_sim(args)
    if sys.platform == "win32":
        os.system("")  # enable ANSI escapes in the Windows console
    recent: list[str] = []
    for _ in range(args.ticks):
        t0 = time.perf_counter()
        frame = await sim.tick()
        if args.headless:
            await sim.settle()  # no wall clock to wait on: let in-flight LLM calls finish
        else:
            sys.stdout.write("\x1b[H\x1b[2J" + render(frame, recent) + "\n")
            sys.stdout.flush()
            await asyncio.sleep(max(0.0, args.tick_ms / 1000 - (time.perf_counter() - t0)))
        if frame["stats"]["population"] == 0:
            print(f"Extinction at tick {frame['tick']}.")
            break
    s = sim.last_frame["stats"]
    print(f"Finished at tick {sim.last_frame['tick']}: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    if hasattr(sim.brain, "calls"):
        print(f"LLM calls={sim.brain.calls} failures={sim.brain.failures}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_sim_args(p)
    p.add_argument("--ticks", type=int, default=1000)
    p.add_argument("--tick-ms", type=int, default=250)
    p.add_argument("--headless", action="store_true", help="no rendering, just run and summarise")
    p.add_argument("--show-prompt", action="store_true")
    args = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
