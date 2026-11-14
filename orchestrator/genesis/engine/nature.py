"""The living world around the villagers: the clock, the seasons and the weather,
fire, cold and illness, elders teaching the young, and the wild animals.

Everything here runs inside ``World.step`` (animals every tick, the rest once per
unit of world time) and draws only on the world's own seeded ``Rng``, so a run
stays reproducible. ``apply`` handles the outside interventions the event
director (``genesis.events``) uses: lighting a fire, an illness, a flood...
"""
from __future__ import annotations

from .defs import (ANIMAL_ID_BASE, ANIMALS, ASH_UNITS, BURN_UNITS, CATCH_ODDS, DAWN, DAY_TICKS, DIRS, DUSK,
                   ELDER_AGE, FIRE_HURT, FLAMMABLE, IMMUNE_UNITS, GOTO_LIMIT, MAX_BLAZES, MAX_WOLVES, OBJECTS, SEASON_DAYS,
                   SEASON_GROWTH, SEASON_WEATHER, SEASONS, SICK_UNITS, SIGHT, START_HOUR, STAT_MAX,
                   TEACH_EVERY, TEACH_XP, TREE_WINTER_GROWTH, WARM_REACH, WATER, WEATHER_UNITS, WEATHERS,
                   WOLF_REST)


def cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


# --- the clock ---------------------------------------------------------------------
def day_part(tick: int) -> float:
    """How far through the day it is: 0 is midnight, 0.5 noon."""
    return ((tick + START_HOUR * DAY_TICKS) % DAY_TICKS) / DAY_TICKS


def day_number(tick: int) -> int:
    """Days since the world began (the first is day 0)."""
    return int((tick + START_HOUR * DAY_TICKS) // DAY_TICKS)


def season_of(tick: int) -> str:
    return SEASONS[(day_number(tick) // SEASON_DAYS) % len(SEASONS)]


def is_night(tick: int) -> bool:
    t = day_part(tick)
    return t < DAWN or t >= DUSK


def light(tick: int) -> float:
    """Daylight from 0 (deep night) to 1 (full day), fading at dawn and dusk."""
    t, fade = day_part(tick), 0.06
    if DAWN + fade <= t < DUSK - fade:
        return 1.0
    if t < DAWN - fade or t >= DUSK + fade:
        return 0.0
    edge = DAWN if t < 0.5 else DUSK
    x = (t - (edge - fade)) / (2 * fade)
    return x if t < 0.5 else 1 - x


def time_words(tick: int) -> str:
    """"morning", "night"...: the time of day, in a word."""
    t = day_part(tick)
    if t < DAWN:
        return "night"
    if t < DAWN + 0.08:
        return "dawn"
    if t < 0.45:
        return "morning"
    if t < 0.6:
        return "midday"
    if t < DUSK - 0.06:
        return "afternoon"
    if t < DUSK + 0.04:
        return "dusk"
    return "night"


def clock(w) -> dict:
    """The time and weather, for the front end and for prompts."""
    day = day_number(w.tick)
    return {"day": day + 1, "season": season_of(w.tick), "day_of_season": day % SEASON_DAYS + 1,
            "part": round(day_part(w.tick), 4), "light": round(light(w.tick), 3), "night": is_night(w.tick),
            "time": time_words(w.tick), "weather": w.weather, "omen": w.omen}


def sight(w) -> int:
    """How far away people can be made out: less at night and in fog or rain."""
    f = WEATHERS[w.weather].sight * (0.6 if is_night(w.tick) else 1.0)
    return max(4, round(SIGHT * f))


def walk_limit(w) -> int:
    """How far people know their way to things: less in the dark and the fog."""
    f = min(1.0, 0.4 + WEATHERS[w.weather].sight) * (0.75 if is_night(w.tick) else 1.0)
    return max(10, round(GOTO_LIMIT * f))


def growth(w, o) -> float:
    """How much an object grows back in one unit of world time (1 = normal)."""
    season = season_of(w.tick)
    weather = WEATHERS[w.weather].growth
    if o.kind in ("tree", "sapling"):
        return (TREE_WINTER_GROWTH if season == "winter" else 1.0) * max(0.5, weather)
    if o.spec.food:
        return SEASON_GROWTH[season] * weather
    return 1.0


# --- weather -----------------------------------------------------------------------
def _pick(w, weights: dict) -> str:
    total = sum(weights.values())
    r = w.rng.below(total)
    for key, n in weights.items():
        r -= n
        if r < 0:
            return key
    return next(iter(weights))


def change_weather(w, phase: int):
    season = season_of(w.tick)
    if season != w.season:
        w.season = season
        w.events.append({"type": "season", "season": season, "npc": -1})
        allowed = SEASON_WEATHER[season]
        if w.weather not in allowed:  # (no snow in spring)
            w.weather_left = 0
    w.weather_left -= 1
    if w.weather_left > 0:
        return
    allowed = SEASON_WEATHER[season]
    old = w.weather
    if not (old in allowed and w.rng.below(100) < 35):  # weather tends to hold for a while
        w.weather = _pick(w, allowed)
    w.weather_left = WEATHER_UNITS
    if w.weather != old:
        w.events.append({"type": "weather", "weather": w.weather, "was": old, "npc": -1})


# --- fire ----------------------------------------------------------------------------
def ignite(w, x: int, y: int, cause: str = "") -> bool:
    """Set fire to whatever grows on a tile. A farm loses its crop instead."""
    if not w.inb(x, y) or len(w.blazes) >= MAX_BLAZES:
        return False
    i = w.idx(x, y)
    o = w.objs.get(i)
    if o is None:
        return False
    if o.kind == "farm" and o.progress >= 100:
        if o.ripe:
            w.harvest(o)
            w.events.append({"type": "burn", "npc": -1, "x": x, "y": y, "what": "farm"})
        return False
    if o.kind not in FLAMMABLE:
        return False
    w.remove(x, y)
    b = w.place("blaze", x, y)
    b.data["left"] = BURN_UNITS
    b.data["was"] = o.kind
    w.blazes.add(i)
    w.events.append({"type": "ignite", "npc": -1, "x": x, "y": y, "what": o.kind, "cause": cause})
    return True


def burn_out(w, i: int, doused_by: int = -1):
    """A fire on tile ``i`` is over: the ground there is ash for a while."""
    x, y = i % w.w, i // w.w
    w.blazes.discard(i)
    w.remove(x, y)
    if w.terrain[i] not in WATER and w.terrain[i] != "ash":
        w.revert[i] = [w.terrain[i], ASH_UNITS]
        w.set_terrain(x, y, "ash")
    if doused_by >= 0:
        w.events.append({"type": "douse", "npc": doused_by, "x": x, "y": y})


def fires(w):
    weather = WEATHERS[w.weather]
    if weather.lightning and w.rng.below(100) < weather.lightning:
        trees = sorted(w.where.get(OBJECTS["tree"].glyph, ()))
        if trees:
            i = trees[w.rng.below(len(trees))]
            w.events.append({"type": "lightning", "npc": -1, "x": i % w.w, "y": i // w.w})
            ignite(w, i % w.w, i // w.w, "lightning")
    for i in sorted(w.blazes):
        if i not in w.blazes:
            continue
        b = w.objs.get(i)
        if b is None or b.kind != "blaze":
            w.blazes.discard(i)
            continue
        x, y = b.x, b.y
        if weather.wet and w.rng.below(100) < 50:  # (the rain puts it out)
            burn_out(w, i)
            continue
        for dx, dy in DIRS:
            if w.inb(x + dx, y + dy) and w.rng.below(100) < weather.spread:
                ignite(w, x + dx, y + dy, "spread")
        for n in w.npcs:
            if n.alive and cheb(n.x, n.y, x, y) <= 1:
                n.health = max(0, n.health - FIRE_HURT)
                w.events.append({"type": "burn", "npc": n.id, "x": x, "y": y})
                if n.health <= 0:
                    w.kill(n, "fire")
        b.data["left"] -= 1
        if b.data["left"] <= 0:
            burn_out(w, i)


def grow_back(w):
    """Ash, flood water and the like return to what the ground was."""
    for i in sorted(w.revert):
        entry = w.revert[i]
        entry[1] -= 1
        if entry[1] <= 0:
            del w.revert[i]
            if w.terrain[i] in WATER and w.occ[i] > 0:
                entry[1] = 1  # (someone is wading there: wait)
                w.revert[i] = entry
                continue
            w.set_terrain(i % w.w, i // w.w, entry[0])


# --- the body: cold, illness, lessons -------------------------------------------------
def warm(w, n) -> bool:
    """Whether something keeps the cold off: a campfire close by, or a home beside them."""
    for o in w.adjacent_objs(n):
        if o.progress >= 100 and (o.spec.home or o.spec.hearth):
            return True
    for yy in range(n.y - WARM_REACH, n.y + WARM_REACH + 1):
        for xx in range(n.x - WARM_REACH, n.x + WARM_REACH + 1):
            if w.inb(xx, yy):
                o = w.objs.get(w.idx(xx, yy))
                if o is not None and o.progress >= 100 and (o.spec.hearth or o.kind == "blaze"):
                    return True
    return False


def bodies(w, phase: int):
    weather = WEATHERS[w.weather]
    cold_season = season_of(w.tick) == "winter" or weather.cold
    for n in list(w.npcs):
        if not n.alive:
            continue
        # the cold: winter (worse at night and in the snow) wears out those with no warmth
        was = n.cold
        n.cold = bool(cold_season) and not warm(w, n)
        if n.cold:
            n.energy = max(0, n.energy - 1 - weather.cold - (1 if is_night(w.tick) and phase % 2 else 0))
            if n.energy < 15:
                n.health = max(0, n.health - 1)
            if w.rng.below(1000) < 1 and not n.sick and not n.immune:  # a chill turns into a fever
                fall_ill(w, n, "cold")
            if not was:
                w.events.append({"type": "cold", "npc": n.id})
        # illness: it saps strength, passes to whoever is close, and runs its course
        n.immune = max(0, n.immune - 1)
        if n.sick:
            n.sick = max(0, n.sick - (2 if n.asleep else 1))
            if phase % 3 == 0:
                n.health = max(0, n.health - (2 if n.age >= ELDER_AGE else 1))
            if phase % 2 == 0:
                n.energy = max(0, n.energy - 1)
            for o in w.npcs:
                if o is not n and o.alive and not o.sick and not o.immune and cheb(o.x, o.y, n.x, n.y) <= 1 \
                        and w.rng.below(100) < CATCH_ODDS:
                    fall_ill(w, o, "caught", n.id)
            if n.health <= 0:
                w.kill(n, "illness")
            elif n.sick == 0:
                n.immune = IMMUNE_UNITS
                w.events.append({"type": "recover", "npc": n.id})
        if n.alive and n.health <= 0:
            w.kill(n, "the cold")
    if phase % TEACH_EVERY == 0:
        teach(w)


def fall_ill(w, n, how: str, source: int = -1, units: int = 0):
    lo, hi = SICK_UNITS
    n.sick = units or lo + w.rng.below(hi - lo + 1)
    w.events.append({"type": "sick", "npc": n.id, "how": how, "source": source})


def teach(w):
    """Elders pass on what they know to the younger people beside them."""
    for e in w.npcs:
        if not e.alive or e.age < ELDER_AGE or not e.skills:
            continue
        best = max(e.skills, key=lambda k: (e.skills[k], k))
        if e.skills[best] < 2:
            continue
        for p in w.npcs:
            if p is not e and p.alive and p.age < ELDER_AGE and cheb(p.x, p.y, e.x, e.y) <= 1 \
                    and p.level(best) < e.skills[best]:
                w.train(p, best, TEACH_XP)
                w.events.append({"type": "teach", "npc": e.id, "target": p.id, "skill": best})
                break  # (one pupil at a time)


# --- wild animals ----------------------------------------------------------------------
class Animal:
    __slots__ = ("id", "kind", "x", "y", "hp", "rest", "scared")

    def __init__(self, id_, kind, x, y):
        self.id, self.kind, self.x, self.y = id_, kind, x, y
        self.hp = ANIMALS[kind].hp
        self.rest = 0      # ticks until a wolf may bite again
        self.scared = 0    # ticks it keeps running after being hunted

    @property
    def spec(self):
        return ANIMALS[self.kind]

    def to_json(self) -> list:
        return [self.id, self.kind, self.x, self.y, self.hp]


def _open(w, x, y) -> bool:
    return w.inb(x, y) and w.walk[w.idx(x, y)] and w.terrain[w.idx(x, y)] not in WATER


def add_animal(w, kind: str, x: int, y: int) -> Animal | None:
    if not _open(w, x, y):
        return None
    a = Animal(ANIMAL_ID_BASE + w.next_animal, kind, x, y)
    w.next_animal += 1
    w.animals[a.id] = a
    return a


def _wild_spot(w, near=None, lo=0, hi=10**9, tries=60):
    """A random open tile of grass or forest (``lo``..``hi`` tiles from ``near``)."""
    for _ in range(tries):
        if near is None:
            x, y = w.rng.below(w.w), w.rng.below(w.h)
        else:
            x = near[0] - hi + w.rng.below(2 * hi + 1)
            y = near[1] - hi + w.rng.below(2 * hi + 1)
        if not _open(w, x, y) or w.terrain[w.idx(x, y)] not in ("grass", "forest", "dirt", "snow"):
            continue
        if near is not None and cheb(x, y, *near) < lo:
            continue
        if any(cheb(x, y, n.x, n.y) < 8 for n in w.npcs):
            continue
        return x, y
    return None


def populate(w):
    """The animals living on the land when the world begins."""
    land = sum(1 for i in range(w.w * w.h) if w.walk[i] and w.terrain[i] not in WATER)
    for kind, spec in ANIMALS.items():
        if not spec.per_tiles:
            continue
        for _ in range(max(2, land // spec.per_tiles)):
            spot = _wild_spot(w)
            if spot:
                add_animal(w, kind, *spot)
    w.wild_cap = {k: sum(1 for a in w.animals.values() if a.kind == k) for k in ANIMALS}


def _step_toward(w, a, tx, ty, away=False):
    best, best_d = None, None
    for dx, dy in DIRS:
        x, y = a.x + dx, a.y + dy
        if not _open(w, x, y):
            continue
        d = abs(x - tx) + abs(y - ty)
        if best is None or (d > best_d if away else d < best_d):
            best, best_d = (x, y), d
    if best is not None and (best_d != abs(a.x - tx) + abs(a.y - ty) or not away):
        a.x, a.y = best


def _wander(w, a):
    dx, dy = DIRS[w.rng.below(4)]
    if _open(w, a.x + dx, a.y + dy):
        a.x, a.y = a.x + dx, a.y + dy


def prowling(w) -> bool:
    """Wolves hunt people at night, and all day long in winter."""
    return is_night(w.tick) or season_of(w.tick) == "winter"


def animals_tick(w):
    if not w.animals:
        return
    people = [n for n in w.npcs if n.alive]
    for a in [w.animals[k] for k in sorted(w.animals)]:
        spec = a.spec
        near = min(people, key=lambda n: (cheb(n.x, n.y, a.x, a.y), n.id), default=None)
        dist = cheb(near.x, near.y, a.x, a.y) if near else 10**9
        a.rest = max(0, a.rest - 1)
        a.scared = max(0, a.scared - 1)
        if spec.hunter:
            if a.hp < spec.hp * 0.4 or not prowling(w):  # hurt, or daylight: slink off
                if dist > 14:
                    del w.animals[a.id]
                    w.events.append({"type": "wolf_gone", "npc": -1, "animal": a.id})
                elif w.rng.below(100) < spec.speed:
                    _step_toward(w, a, near.x, near.y, away=True)
                continue
            if near is not None and dist <= 1:
                if a.rest == 0:
                    bite(w, a, near)
                continue
            if near is not None and dist <= 12 and w.rng.below(100) < spec.speed:
                _step_toward(w, a, near.x, near.y)
            elif w.rng.below(100) < 15:
                _wander(w, a)
            continue
        if near is not None and (dist <= spec.shy or a.scared):
            if w.rng.below(100) < min(95, spec.speed + 30):
                _step_toward(w, a, near.x, near.y, away=True)
        elif w.rng.below(100) < 8:  # grazing
            _wander(w, a)


def bite(w, a, n):
    spec = a.spec
    a.rest = WOLF_REST
    hit = w.rng.below(100) < max(35, 65 - 3 * (n.attrs["agility"] - 5) - 2 * n.level("combat"))
    dmg = spec.damage[0] + w.rng.below(spec.damage[1] - spec.damage[0] + 1) if hit else 0
    n.health = max(0, n.health - dmg)
    w.events.append({"type": "mauled", "npc": n.id, "animal": a.id, "kind": a.kind, "hit": hit, "damage": dmg})
    if n.health <= 0:
        w.kill(n, "wolves")
        return
    if not n.asleep and w.rng.below(100) < 50:  # anyone awake lashes out at what is biting them
        hurt = max(1, 6 + w.rng.below(6) + (n.attrs["strength"] - 5) + n.level("combat") // 2
                   + (12 if n.have("spear") else 0))
        a.hp -= hurt
        w.train(n, "combat", 3)
        killed = a.hp <= 0
        w.events.append({"type": "hunt", "npc": n.id, "animal": a.id, "what": a.kind, "hit": True, "damage": hurt,
                         "killed": killed, "x": a.x, "y": a.y, "defending": True})
        if killed:
            del w.animals[a.id]
            w.drop(a.x, a.y, {"food": spec.food}, n.id)


def animals_world_time(w, phase: int):
    """Births in the warm months, and wolves drawn out of the wild by night and winter."""
    season = season_of(w.tick)
    counts = {k: 0 for k in ANIMALS}
    for a in w.animals.values():
        counts[a.kind] += 1
    if phase % 10 == 0 and season in ("spring", "summer"):
        for kind, cap in w.wild_cap.items():
            if not cap or counts[kind] >= cap:
                continue
            kin = sorted(a.id for a in w.animals.values() if a.kind == kind)
            if len(kin) >= 2 and w.rng.below(100) < 35:
                mum = w.animals[kin[w.rng.below(len(kin))]]
                spot = _wild_spot(w, (mum.x, mum.y), 1, 3, 12)
                if spot:
                    add_animal(w, kind, *spot)
            elif len(kin) < 2 and w.rng.below(100) < 10:  # a few wander in from elsewhere
                spot = _wild_spot(w)
                if spot:
                    add_animal(w, kind, *spot)
    if prowling(w) and counts["wolf"] == 0 and w.rng.below(1000) < 4:
        homes = [v for v in w.villages.values() if not v.abandoned and len(w.members(v.id)) >= 4]
        if homes:
            v = homes[w.rng.below(len(homes))]
            wolf_pack(w, v.x, v.y, 2 + w.rng.below(2))


def wolf_pack(w, x, y, size: int) -> int:
    """Wolves come out of the wild some way off from (x, y). Returns how many."""
    spot = _wild_spot(w, (x, y), 14, 18, 120)
    if spot is None:
        return 0
    have = sum(1 for a in w.animals.values() if a.kind == "wolf")
    made = 0
    for k in range(min(size, MAX_WOLVES - have)):
        if add_animal(w, "wolf", spot[0] + (k % 2), spot[1] + (k // 2)) or add_animal(w, "wolf", *spot):
            made += 1
    if made:
        w.events.append({"type": "wolves", "npc": -1, "x": spot[0], "y": spot[1], "n": made})
    return made


# --- the hooks World calls -------------------------------------------------------------
def on_tick(w):
    animals_tick(w)


def on_world_time(w, phase: int):
    change_weather(w, phase)
    fires(w)
    grow_back(w)
    bodies(w, phase)
    animals_world_time(w, phase)
    if w.omen and w.omen.get("until", 0) <= w.tick:
        w.omen = None


def flood(w, x: int, y: int, reach: int, units: int) -> int:
    """Water rises over the low land near (x, y): bare tiles within ``reach`` of water
    become shallow water for ``units`` of world time. Returns how many tiles flooded."""
    wet = []
    for yy in range(max(0, y - reach * 3), min(w.h, y + reach * 3 + 1)):
        for xx in range(max(0, x - reach * 3), min(w.w, x + reach * 3 + 1)):
            i = w.idx(xx, yy)
            if w.terrain[i] in WATER or w.terrain[i] in ("peak", "stone") or i in w.objs or i in w.revert:
                continue
            if any(w.inb(xx + dx, yy + dy) and w.terrain[w.idx(xx + dx, yy + dy)] in WATER
                   for dx in range(-reach, reach + 1) for dy in range(-reach, reach + 1)):
                wet.append(i)
    for i in wet:
        w.revert[i] = [w.terrain[i], units]
        w.set_terrain(i % w.w, i // w.w, "shallow")
    return len(wet)


def apply(w, cmd: dict) -> bool:
    """Interventions from outside the rules (the event director, the player)."""
    op = cmd.get("op")
    x, y = cmd.get("x", -1), cmd.get("y", -1)
    n = w.by_id.get(cmd.get("npc", -1))
    if op == "ignite":
        return ignite(w, x, y, cmd.get("cause", "event"))
    if op == "sick" and n is not None and n.alive:
        fall_ill(w, n, cmd.get("how", "plague"), units=int(cmd.get("units", 0)))
        return True
    if op == "animal" and cmd.get("kind") in ANIMALS:
        if cmd["kind"] == "wolf":
            return wolf_pack(w, x, y, int(cmd.get("n", 3))) > 0
        return add_animal(w, cmd["kind"], x, y) is not None
    if op == "ripen":  # a bumper harvest: every food plant around (x, y) is ready at once
        r, done = int(cmd.get("r", 8)), 0
        for o in list(w.objs.values()):
            if o.spec.food and not o.ripe and o.progress >= 100 and cheb(o.x, o.y, x, y) <= r:
                o.timer = 0
                done += 1
        return done > 0
    if op == "store":
        v = w.villages.get(cmd.get("village", -1))
        if v is None:
            return False
        item, k = cmd.get("item"), int(cmd.get("n", 0))
        v.stock[item] = max(0, v.stock.get(item, 0) + k)
        if not v.stock[item]:
            del v.stock[item]
        return True
    if op == "flood":
        return flood(w, x, y, int(cmd.get("r", 1)), int(cmd.get("units", 60))) > 0
    if op == "omen":
        w.omen = {"kind": str(cmd.get("kind", "comet")), "until": w.tick + int(cmd.get("ticks", 200))}
        return True
    if op == "weather" and cmd.get("weather") in WEATHERS:
        old, w.weather = w.weather, cmd["weather"]
        w.weather_left = int(cmd.get("units", WEATHER_UNITS))
        if old != w.weather:
            w.events.append({"type": "weather", "weather": w.weather, "was": old, "npc": -1})
        return True
    if op == "heal_all":  # (testing)
        for p in w.npcs:
            p.health = STAT_MAX
        return True
    return False


