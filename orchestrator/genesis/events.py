"""The event director: now and then, something happens to a village.

Wildfire, plague, a bumper harvest and the festival after it, a wandering
trader, migrants looking for a home, a wolf pack, a comet or an eclipse, a
flood, a stranger with a new belief. Events are seeded (the same world tells
the same story) and paced: none in the first minutes, a long gap after each,
and more often the more people there are to happen to.

Every event becomes a chronicle line (so village news, which villagers gossip
about) and a memory for everyone in the village it touched, so LLM villagers
talk about it. Effects go through ``World.apply`` (see ``engine.nature.apply``).
"""
from __future__ import annotations

import random

from .engine.defs import ITEMS, POP_LIMIT, WEATHERS
from .traits import BELIEFS, VOICES

FIRST_EVENT = 600      # ticks before anything happens (~2 minutes)
CHECK_EVERY = 120      # ticks between rolls of the dice
MIN_GAP = 700          # ticks after one event before the next can happen


def _cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


class Director:
    def __init__(self, seed: int):
        self.rng = random.Random(seed * 31_337 + 5)
        self.last = -10**9
        self.happened: list[dict] = []  # this tick's events, for the front end: {kind, text, x, y}

    # --- helpers ---------------------------------------------------------------------
    def _villages(self, sim, least: int = 1) -> list[dict]:
        return [v for v in sim.state.get("villages", []) if not v["abandoned"] and v["people"] >= least]

    def _members(self, sim, vid: int) -> list[dict]:
        return [n for n in sim.state["npcs"] if n["alive"] and n.get("village") == vid]

    def _tell(self, sim, vid: int | None, text: str, valence: int, tick: int):
        """Everyone touched remembers it (the whole world, if ``vid`` is None)."""
        for n in sim.state["npcs"]:
            if n["alive"] and (vid is None or n.get("village") == vid):
                sim._remember(n["id"], -1, tick, "event", text, valence)

    def _spot(self, sim, x: int, y: int, lo: int, hi: int, want=None):
        """A tile ``lo``..``hi`` from (x, y) that ``want(world, x, y)`` accepts."""
        w = sim.engine.world
        want = want or (lambda w, xx, yy: w.passable(xx, yy) and not w.occupied(xx, yy))
        for _ in range(80):
            xx = x + self.rng.randint(-hi, hi)
            yy = y + self.rng.randint(-hi, hi)
            if w.inb(xx, yy) and lo <= _cheb(xx, yy, x, y) <= hi and want(w, xx, yy):
                return xx, yy
        return None

    # --- the events -------------------------------------------------------------------
    def _wildfire(self, sim, v, tick):
        w = sim.engine.world
        spot = self._spot(sim, v["x"], v["y"], v["radius"] - 2, v["radius"] + 8,
                          lambda w, x, y: (o := w.obj_at(x, y)) is not None and o.kind == "tree")
        if spot is None:
            return None
        sim.enqueue({"op": "ignite", "x": spot[0], "y": spot[1], "cause": "wildfire"})
        text = f"A wildfire broke out in the trees near {sim.village_name(v['id'])}."
        self._tell(sim, v["id"], text, -2, tick)
        return "wildfire", f"🔥 Wildfire! The trees near {sim.village_name(v['id'])} are burning", spot

    def _plague(self, sim, v, tick):
        people = self._members(sim, v["id"])
        sick = self.rng.sample(people, min(len(people), 1 + self.rng.randrange(2)))
        for n in sick:
            sim.enqueue({"op": "sick", "npc": n["id"], "how": "plague", "units": 60 + self.rng.randrange(30)})
        names = " and ".join(sim.meta[n["id"]].name for n in sick)
        self._tell(sim, v["id"], f"A fever is going round {sim.village_name(v['id'])}: {names} came down with it.",
                   -2, tick)
        return "plague", f"🤒 A fever spreads in {sim.village_name(v['id'])}: {names} fall ill", (v["x"], v["y"])

    def _harvest(self, sim, v, tick):
        name = sim.village_name(v["id"])
        sim.enqueue({"op": "ripen", "x": v["x"], "y": v["y"], "r": v["radius"] + 3})
        if v["buildings"].get("storehouse"):
            sim.enqueue({"op": "store", "village": v["id"], "item": "food", "n": 12})
        for n in self._members(sim, v["id"]):  # the festival: everyone's need for company is met
            sim.enqueue({"op": "stat", "npc": n["id"], "stat": "social", "value": 5})
        self._tell(sim, v["id"], f"A bumper harvest in {name}! We held a festival by the fire and danced all night.",
                   3, tick)
        return "festival", f"🎉 A bumper harvest in {name}, and a festival to celebrate it", (v["x"], v["y"])

    def _trader(self, sim, v, tick):
        name = sim.village_name(v["id"])
        goods = self.rng.choice([("axe", 1), ("pickaxe", 1), ("rod", 1), ("spear", 1), ("stone", 8)])
        if v["buildings"].get("storehouse"):
            sim.enqueue({"op": "store", "village": v["id"], "item": goods[0], "n": goods[1]})
            if v["stock"].get("wood", 0) >= 6:
                sim.enqueue({"op": "store", "village": v["id"], "item": "wood", "n": -6})
            where = "the storehouse"
        else:
            people = self._members(sim, v["id"])
            who = self.rng.choice(people)
            sim.enqueue({"op": "give", "npc": who["id"], "item": goods[0], "n": goods[1]})
            where = sim.meta[who["id"]].name
        what = f"{goods[1]} {ITEMS[goods[0]].label}"
        self._tell(sim, v["id"], f"A wandering trader came through {name} and left {what} with {where}.", 2, tick)
        return "trader", f"🧺 A wandering trader passes through {name}, leaving {what}", (v["x"], v["y"])

    def _migrants(self, sim, v, tick, belief: int | None = None):
        alive = sum(1 for n in sim.state["npcs"] if n["alive"])
        if alive >= POP_LIMIT - 4:
            return None
        k = 1 if belief is not None else 2 + self.rng.randrange(2)
        spots = []
        for _ in range(k):
            s = self._spot(sim, v["x"], v["y"], v["radius"] + 2, v["radius"] + 6)
            if s and s not in spots:
                spots.append(s)
        if not spots:
            return None
        for x, y in spots:
            cmd = {"op": "spawn", "x": x, "y": y, "village": v["id"], "why": "migrant"}
            if belief is not None:
                cmd["persona"] = {"belief": belief, "voice": self.rng.randrange(len(VOICES))}
            sim.enqueue(cmd)
        name = sim.village_name(v["id"])
        if belief is not None:
            text = f"A stranger came to {name}, someone who {BELIEFS[belief][0]}."
            self._tell(sim, v["id"], text, 0, tick)
            return "stranger", f"🧙 A stranger arrives in {name}, one who {BELIEFS[belief][0]}", spots[0]
        self._tell(sim, v["id"], f"{len(spots)} newcomers walked into {name} looking for a place to live.", 1, tick)
        return "migrants", f"🧳 {len(spots)} migrants arrive in {name}, looking for a home", spots[0]

    def _stranger(self, sim, v, tick):
        held = {m.persona.get("belief") for i, m in sim.meta.items()
                if m.persona and any(n["id"] == i and n["alive"] for n in sim.state["npcs"])}
        new = [i for i in range(len(BELIEFS)) if i not in held]
        if not new:
            return None
        return self._migrants(sim, v, tick, belief=self.rng.choice(new))

    def _wolves(self, sim, v, tick):
        sim.enqueue({"op": "animal", "kind": "wolf", "x": v["x"], "y": v["y"], "n": 3})
        name = sim.village_name(v["id"])
        self._tell(sim, v["id"], f"Wolves were howling close to {name} in the dark.", -2, tick)
        return "wolves", f"🐺 A wolf pack closes in on {name}", (v["x"], v["y"])

    def _omen(self, sim, v, tick):
        night = sim.state["clock"]["night"]
        kind = "comet" if night else "eclipse"
        sim.enqueue({"op": "omen", "kind": kind, "ticks": 160 if kind == "comet" else 90})
        text = ("A comet with a burning tail crossed the night sky. Everyone saw it." if kind == "comet" else
                "The sun went dark in the middle of the day, then came back. Nobody knows what it means.")
        self._tell(sim, None, text, 0, tick)
        line = "☄️ A comet blazes across the night sky" if kind == "comet" else "🌑 The sun goes dark: an eclipse"
        return kind, line, None

    def _flood(self, sim, v, tick):
        w = sim.engine.world
        r = v["radius"] + 4
        wet = any(w.terrain[w.idx(x, y)] in ("shallow", "deep")
                  for y in range(max(0, v["y"] - r), min(w.h, v["y"] + r + 1))
                  for x in range(max(0, v["x"] - r), min(w.w, v["x"] + r + 1)))
        if not wet:
            return None
        sim.enqueue({"op": "flood", "x": v["x"], "y": v["y"], "r": 1, "units": 50})
        name = sim.village_name(v["id"])
        self._tell(sim, v["id"], f"The water rose and flooded the low ground around {name}.", -1, tick)
        return "flood", f"🌊 The river floods the low ground around {name}", (v["x"], v["y"])

    # --- the director ------------------------------------------------------------------
    def tick(self, sim, now: int) -> list[str]:
        """Maybe make something happen. Returns chronicle lines."""
        self.happened = []
        if now < FIRST_EVENT or now % CHECK_EVERY or now - self.last < MIN_GAP:
            return []
        villages = self._villages(sim)
        if not villages:
            return []
        people = sum(v["people"] for v in villages)
        if self.rng.random() >= min(0.45, 0.08 + 0.015 * people):
            return []
        clock = sim.state["clock"]
        season, weather, night = clock["season"], clock["weather"], clock["night"]
        wet = WEATHERS[weather].wet
        options = [  # (weight, event, needs at least this many people in the village)
            ((30 if weather == "drought" else 10 if season == "summer" else 4) * (not wet), self._wildfire, 1),
            (12 if season == "winter" else 6, self._plague, 4),
            (14 if season == "autumn" else 3 if season == "summer" else 0, self._harvest, 2),
            (6, self._trader, 2),
            (9 if season in ("spring", "summer") else 4, self._migrants, 2),
            ((14 if season == "winter" else 7) if night else 0, self._wolves, 3),
            (4, self._omen, 1),
            (10 if season == "spring" and weather in ("rain", "storm") else 0, self._flood, 2),
            (4, self._stranger, 3),
        ]
        options = [(wt, fn, least) for wt, fn, least in options if wt > 0]
        for _ in range(3):  # (an event that cannot happen here gives way to another)
            total = sum(wt for wt, _, _ in options)
            if not total:
                return []
            r = self.rng.random() * total
            for wt, fn, least in options:
                r -= wt
                if r < 0:
                    break
            options = [o for o in options if o[1] != fn]
            homes = [v for v in villages if v["people"] >= least]
            if not homes:
                continue
            v = self.rng.choice(homes)
            out = fn(sim, v, now)
            if out:
                kind, line, where = out
                self.last = now
                self.happened.append({"kind": kind, "text": line, "x": where[0] if where else None,
                                      "y": where[1] if where else None})
                return [line]
        return []
