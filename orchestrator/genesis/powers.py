"""The god's powers over the land: plant and clear, dig and fill, gifts from
the sky, weather, fire and lightning, buildings, new people, wild animals, and
the director's events on demand.

Each power is checked here against the world as it stands (so the player hears
at once why it cannot be done), then carried out through ``World.apply`` at the
start of the next tick. Villagers close enough to see a miracle remember it,
and come to believe in whoever is doing them.

Two ways to play, chosen on the start screen:
  * sandbox: every power is free.
  * deity:   powers cost Faith, which comes from believers (those who have
             heard the voice from the sky or seen a miracle), from prayers,
             and from every shrine the villages raise.
"""
from __future__ import annotations

from dataclasses import dataclass

from .engine.defs import OBJECTS, TERRAIN, WATER

MODES = ("sandbox", "deity")
FAITH_START = 50        # a new god's faith (deity mode)
FAITH_MAX = 500
FAITH_EVERY = 120       # ticks between each round of faith from believers and shrines
FAITH_PRAYER = 2        # faith for every prayer said
WITNESS_REACH = 10      # how far away a villager can see a miracle happen


@dataclass(frozen=True)
class Power:
    key: str
    label: str
    emoji: str
    group: str        # where it sits on the toolbar: land, gifts, sky, build, life, events, people
    cost: int         # faith (deity mode)
    target: str       # "tile" (click the ground), "npc" (click someone), "sky" (no target)
    blurb: str
    paint: bool = False   # drag across the land to do it to every tile passed over
    seen: str = ""        # what a witness remembers ("" = nothing to see)


def _p(*a, **k):
    return Power(*a, **k)


_BUILD = ("fire", "hut", "farm", "well", "storehouse", "workshop", "house", "shrine")
_BUILD_COST = {"fire": 6, "hut": 15, "farm": 8, "well": 15, "storehouse": 20, "workshop": 20, "house": 25,
               "shrine": 20}
_EVENTS = {  # power key -> (director method, label, emoji, cost, blurb)
    "ev_wildfire": ("_wildfire", "Wildfire", "🔥", 15, "Set the trees near a village burning"),
    "ev_plague": ("_plague", "Fever", "🤒", 20, "A fever goes round a village"),
    "ev_harvest": ("_harvest", "Bumper harvest", "🎉", 25, "Every plant ripens at once, and they hold a festival"),
    "ev_trader": ("_trader", "Trader", "🧺", 15, "A wandering trader brings tools or stone"),
    "ev_migrants": ("_migrants", "Migrants", "🧳", 30, "Two or three newcomers arrive looking for a home"),
    "ev_stranger": ("_stranger", "Stranger", "🧙", 25, "Someone arrives with a belief nobody holds"),
    "ev_wolves": ("_wolves", "Wolf pack", "🐺", 15, "Wolves close in on a village"),
    "ev_omen": ("_omen", "Omen", "☄️", 10, "A comet by night, an eclipse by day"),
    "ev_flood": ("_flood", "Flood", "🌊", 15, "The water rises over the low ground (needs water nearby)"),
}

POWERS: dict[str, Power] = {p.key: p for p in (
    # the land
    _p("tree", "Tree", "🌳", "land", 1, "tile", "Grow a tree", paint=True,
       seen="A tree sprang up out of the bare ground in front of me."),
    _p("bush", "Berry bush", "🫐", "land", 2, "tile", "Grow a bush full of berries", paint=True,
       seen="A berry bush grew out of nothing, already full of fruit."),
    _p("rock", "Rock", "🪨", "land", 1, "tile", "Raise a rock out of the ground", paint=True,
       seen="A rock pushed up out of the earth all by itself."),
    _p("dig", "Water", "💧", "land", 2, "tile", "Dig out water (again for deep water)", paint=True,
       seen="The ground sank and filled with water before my eyes."),
    _p("fill", "Land", "🟫", "land", 2, "tile", "Raise land out of the water, or grass over bare ground", paint=True,
       seen="Dry land rose up out of the water."),
    _p("clear", "Clear", "🧹", "land", 1, "tile", "Take away whatever stands there", paint=True,
       seen="Something just vanished into thin air."),
    # gifts
    _p("food", "Food", "🍖", "gifts", 3, "tile", "Drop food from the sky", seen="Food fell out of the sky."),
    _p("wood", "Wood", "🪵", "gifts", 2, "tile", "Drop wood from the sky", seen="Logs fell out of the sky."),
    _p("stone", "Stone", "🪨", "gifts", 2, "tile", "Drop stone from the sky", seen="Stones rained down out of the sky."),
    # the sky
    _p("rain", "Rain", "🌧️", "sky", 12, "sky", "Bring rain: plants grow, fires go out"),
    _p("sun", "Clear skies", "☀️", "sky", 12, "sky", "Clear the skies"),
    _p("storm", "Storm", "⛈️", "sky", 18, "sky", "A thunderstorm, with lightning"),
    _p("snow", "Snow", "🌨️", "sky", 12, "sky", "Snow (cold for anyone without a fire)"),
    _p("strike", "Lightning", "⚡", "sky", 8, "tile", "Strike the ground: trees catch fire, anyone there dies",
       seen="Lightning came down out of nowhere and struck the ground."),
    _p("fire", "Fire", "🔥", "sky", 6, "tile", "Set a tree, bush or crop alight",
       seen="A tree burst into flames all by itself."),
    # buildings (given whole, to whichever village's land they stand on)
    *(_p(f"b_{k}", OBJECTS[k].place.capitalize(), {"hut": "🛖"}.get(k, OBJECTS[k].emoji), "build", _BUILD_COST[k], "tile",
         f"Raise {OBJECTS[k].label}, finished", seen=f"{OBJECTS[k].label.capitalize()} appeared out of nowhere.")
      for k in _BUILD),
    # life
    _p("settler", "Settler", "🧍", "life", 25, "tile", "A new person appears",
       seen="Someone appeared out of thin air."),
    _p("deer", "Deer", "🦌", "life", 3, "tile", "A deer, to be hunted"),
    _p("rabbit", "Rabbit", "🐇", "life", 2, "tile", "A rabbit"),
    _p("wolves", "Wolves", "🐺", "life", 10, "tile", "A pack of wolves"),
    # the director's events, on demand (at the village nearest the click)
    *(_p(k, label, emoji, "events", cost, "tile", blurb) for k, (_, label, emoji, cost, blurb) in _EVENTS.items()),
    # on a villager
    _p("heal", "Heal", "❤️", "people", 5, "npc", "Heal their wounds and tiredness, and any fever"),
    _p("feed", "Feed", "🍖", "people", 3, "npc", "Fill their belly and give them food"),
    _p("bless", "Bless", "✨", "people", 8, "npc", "Health, food, rest and company all full, and a little wiser"),
    _p("smite", "Smite", "⚡", "people", 10, "npc", "Strike them down with lightning"),
)}


def client_powers() -> list[dict]:
    """The toolbar, for the front end."""
    return [{"key": p.key, "label": p.label, "emoji": p.emoji, "group": p.group, "cost": p.cost,
             "target": p.target, "blurb": p.blurb, "paint": p.paint} for p in POWERS.values()]


# --- faith ---------------------------------------------------------------------------
def earn(sim, events: list[dict], now: int):
    """Faith from prayers as they are said, and now and then from believers and shrines."""
    if sim.cfg.god_mode != "deity":
        return
    gain = FAITH_PRAYER * sum(1 for e in events if e["type"] == "pray")
    if now % FAITH_EVERY == 0:
        living = {n["id"] for n in sim.alive()}
        sim.believers &= living
        shrines = sum(1 for o in sim.engine.world.objs.values() if o.kind == "shrine" and o.progress >= 100)
        gain += len(sim.believers) + 3 * shrines
    sim.faith = min(FAITH_MAX, sim.faith + gain)


def _witness(sim, x: int, y: int, text: str, tick: int):
    """Everyone close enough sees it happen: they remember it, and believe."""
    for n in sim.alive():
        if max(abs(n["x"] - x), abs(n["y"] - y)) <= WITNESS_REACH:
            sim._remember(n["id"], -1, tick, "miracle", text, 0)
            sim.believers.add(n["id"])


# --- using a power -----------------------------------------------------------------------
def use(sim, key: str, x: int = -1, y: int = -1, npc: int = -1) -> tuple[bool, str]:
    """Use a power. Returns (done, what to tell the player)."""
    p = POWERS.get(key)
    if p is None:
        return False, "No such power."
    if sim.possessed is not None:
        return False, "No god powers while you are in someone's body."
    deity = sim.cfg.god_mode == "deity"
    if deity and sim.faith < p.cost:
        return False, f"Not enough faith: {p.label} takes {p.cost}, you have {sim.faith}."
    w = sim.engine.world
    if p.target == "tile" and not w.inb(x, y):
        return False, "That is off the edge of the world."
    ok, msg = _HANDLERS[p.group if p.group in ("build", "events") else p.key](sim, w, p, x, y, npc)
    if not ok:
        return False, msg
    if deity:
        sim.faith -= p.cost
    tick = sim.state["tick"]
    if p.seen and p.target == "tile":
        _witness(sim, x, y, p.seen, tick)
    return True, msg


def _land(w, x, y) -> str:
    return w.terrain[w.idx(x, y)]


def _nobody(w, x, y) -> bool:
    return not w.occupied(x, y)


def _grow(kind):
    def go(sim, w, p, x, y, npc):
        t = TERRAIN[_land(w, x, y)]
        if t.key in WATER or not t.passable:
            return False, f"Nothing grows on {t.label}."
        if w.obj_at(x, y) is not None:
            return False, "Something already stands there."
        if not _nobody(w, x, y):
            return False, "Someone is standing there."
        sim.enqueue({"op": "place", "kind": kind, "x": x, "y": y, "ripe": True})
        return True, ""
    return go


def _dig(sim, w, p, x, y, npc):
    t = _land(w, x, y)
    if t == "deep":
        return False, "It is as deep as water goes."
    if w.obj_at(x, y) is not None:
        return False, "Clear what stands there first."
    if not _nobody(w, x, y):
        return False, "Someone is standing there."
    sim.enqueue({"op": "terrain", "kind": "deep" if t == "shallow" else "shallow", "x": x, "y": y})
    return True, ""


def _fill(sim, w, p, x, y, npc):
    t = _land(w, x, y)
    to = {"deep": "shallow", "shallow": "grass", "sand": "grass", "dirt": "grass", "ash": "grass",
          "stone": "dirt", "path": "grass"}.get(t)
    if to is None:
        return False, f"There is no raising {TERRAIN[t].label}."
    sim.enqueue({"op": "terrain", "kind": to, "x": x, "y": y})
    return True, ""


def _clear(sim, w, p, x, y, npc):
    o = w.obj_at(x, y)
    if o is None:
        return False, "There is nothing there to clear."
    if o.kind == "blaze":
        w.blazes.discard(w.idx(x, y))  # (the fire goes with it)
    sim.enqueue({"op": "clear", "x": x, "y": y})
    if (x, y) in sim.vacant:
        sim.vacant.discard((x, y))
    return True, ""


def _gift(item, n):
    def go(sim, w, p, x, y, npc):
        if not w.passable(x, y):
            return False, "It would be lost there."
        sim.enqueue({"op": "drop", "item": item, "n": n, "x": x, "y": y})
        return True, f"{n} {item} falls from the sky."
    return go


def _weather(kind, line):
    def go(sim, w, p, x, y, npc):
        if w.weather == kind:
            return False, f"It is already {line}."
        sim.enqueue({"op": "weather", "weather": kind, "units": 40})
        for n in sim.alive():
            sim.believers.add(n["id"])  # (everyone sees the sky answer)
        return True, ""
    return go


def _strike(sim, w, p, x, y, npc):
    sim.enqueue({"op": "strike", "x": x, "y": y})
    return True, ""


def _fire(sim, w, p, x, y, npc):
    o = w.obj_at(x, y)
    if o is None or (o.kind not in ("tree", "bush", "fruit", "sapling", "farm")):
        return False, "There is nothing there that would burn."
    sim.enqueue({"op": "ignite", "x": x, "y": y, "cause": "god"})
    return True, ""


def _build(sim, w, p, x, y, npc):
    kind = p.key[2:]
    if not w.free(x, y):
        return False, "It needs open, dry ground with nothing and nobody on it."
    if OBJECTS[kind].hearth and w.village_at(x, y) is None:
        sim.enqueue({"op": "found", "x": x, "y": y})  # a fire lit in the wild: a new village around it
    sim.enqueue({"op": "place", "kind": kind, "x": x, "y": y})
    if OBJECTS[kind].home:
        sim.vacant.add((x, y))  # (a home for whoever needs one)
    if OBJECTS[kind].stat:
        sim.totals[OBJECTS[kind].stat] = sim.totals.get(OBJECTS[kind].stat, 0) + 1
    v = w.village_at(x, y)
    where = f" in {sim.village_name(v.id)}" if v is not None else ""
    sim.god_log.append(f"✨ The gods raise {OBJECTS[kind].label}{where}")
    return True, ""


def _settler(sim, w, p, x, y, npc):
    if not w.passable(x, y) or _land(w, x, y) in WATER:
        return False, "Nobody could stand there."
    v = w.village_at(x, y)
    sim.enqueue({"op": "spawn", "x": x, "y": y, "village": v.id if v is not None else -1, "why": "god"})
    return True, ""


def _animal(kind):
    def go(sim, w, p, x, y, npc):
        if not w.passable(x, y) or _land(w, x, y) in WATER:
            return False, "No animal could stand there."
        sim.enqueue({"op": "animal", "kind": kind, "x": x, "y": y, **({"n": 3} if kind == "wolf" else {})})
        if kind == "wolf":
            sim.god_log.append("🐺 Wolves come out of the wild")
        return True, ""
    return go


def _event(sim, w, p, x, y, npc):
    villages = [v for v in sim.state.get("villages", []) if not v["abandoned"] and v["people"] > 0]
    if not villages:
        return False, "There is no village for it to happen to."
    v = min(villages, key=lambda v: max(abs(v["x"] - x), abs(v["y"] - y)))
    fn = getattr(sim.director, _EVENTS[p.key][0])
    out = fn(sim, v, sim.state["tick"])
    if not out:
        return False, f"It cannot happen at {sim.village_name(v['id'])} right now."
    kind, line, where = out
    sim.director.forced.append({"kind": kind, "text": line, "x": where[0] if where else None,
                                "y": where[1] if where else None})
    sim.god_log.append(line)
    return True, ""


def _npc(sim, nid):
    return next((n for n in sim.alive() if n["id"] == nid), None)


def _on_npc(power):
    def go(sim, w, p, x, y, npc):
        n = _npc(sim, npc)
        if n is None:
            return False, "They are not there any more."
        name = sim.meta[npc].name
        tick = sim.state["tick"]
        if power == "heal":
            for stat in ("health", "energy"):
                sim.enqueue({"op": "stat", "npc": npc, "stat": stat, "value": 100})
            sim.enqueue({"op": "cure", "npc": npc})
            sim.god_log.append(f"❤️ The gods heal {name}")
            sim._remember(npc, -1, tick, "miracle", "A warmth came over me out of nowhere and my hurts went away.", 1)
        elif power == "feed":
            sim.enqueue({"op": "stat", "npc": npc, "stat": "hunger", "value": 100})
            sim.enqueue({"op": "give", "npc": npc, "item": "food", "n": 3})
            sim.god_log.append(f"🍖 The gods feed {name}")
            sim._remember(npc, -1, tick, "miracle", "Food appeared in my hands out of nowhere.", 1)
        elif power == "bless":
            for stat in ("health", "energy", "hunger", "social"):
                sim.enqueue({"op": "stat", "npc": npc, "stat": stat, "value": 100})
            sim.enqueue({"op": "cure", "npc": npc})
            body = w.by_id.get(npc)
            if body is not None:
                sim.enqueue({"op": "attr", "npc": npc, "attr": "intelligence", "value": body.attrs["intelligence"] + 1})
            sim.god_log.append(f"✨ The gods bless {name}")
            sim._remember(npc, -1, tick, "miracle", "I was blessed by the gods. I feel whole, and wiser.", 2)
        else:
            sim.enqueue({"op": "kill", "npc": npc, "cause": "lightning"})
        _witness(sim, n["x"], n["y"], {"heal": f"I saw {name} healed by the gods.",
                                       "feed": f"I saw food fall into {name}'s hands from nowhere.",
                                       "bless": f"I saw {name} blessed by the gods: they glowed.",
                                       "smite": f"I saw the gods strike {name} down with lightning."}[power], tick)
        if power == "smite":
            sim.believers.discard(npc)
        else:
            sim.believers.add(npc)
        return True, ""
    return go


_HANDLERS = {
    "tree": _grow("tree"), "bush": _grow("bush"), "rock": _grow("rock"),
    "dig": _dig, "fill": _fill, "clear": _clear,
    "food": _gift("food", 5), "wood": _gift("wood", 5), "stone": _gift("stone", 4),
    "rain": _weather("rain", "raining"), "sun": _weather("clear", "clear"), "storm": _weather("storm", "storming"),
    "snow": _weather("snow", "snowing"), "strike": _strike, "fire": _fire,
    "build": _build, "settler": _settler,
    "deer": _animal("deer"), "rabbit": _animal("rabbit"), "wolves": _animal("wolf"),
    "events": _event,
    "heal": _on_npc("heal"), "feed": _on_npc("feed"), "bless": _on_npc("bless"), "smite": _on_npc("smite"),
}
assert set(_HANDLERS) >= {p.key for p in POWERS.values() if p.group not in ("build", "events")}
