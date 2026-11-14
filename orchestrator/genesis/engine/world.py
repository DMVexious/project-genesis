"""The world: terrain, the objects standing on it, the NPCs, and time.

The world is layered. ``terrain`` is the ground; ``objs`` holds what stands on
it (rocks, trees, buildings), each an ``Obj`` with its own state. ``glyphs``
is a one-character-per-tile summary of both, kept up to date on every change;
path-finding and the terminal view read it.

Everything random goes through one seeded ``Rng``, so a seed and a sequence of
choices always produce the same world.
"""
from __future__ import annotations

from collections import Counter

from . import mapgen
from .defs import (ATTR_MAX, ATTR_MIN, ATTRS, DROP_TTL, ELDER_AGE, GOTO_BIT, ITEMS, MIN_FERTILITY, OBJECTS,
                   OLD_AGE_ODDS, SKILL_MAX, SKILLS, START_AGE, STAT_MAX, TERRAIN, WALK_EXERCISE, WATER, WET_BONUS,
                   WET_REACH, attr_need, learn_rate, skill_need)
from .npc import Npc

MASK64 = (1 << 64) - 1


class Rng:
    """SplitMix64."""

    def __init__(self, seed: int):
        self.s = seed & MASK64

    def next(self) -> int:
        self.s = (self.s + 0x9E3779B97F4A7C15) & MASK64
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next() % n


class Obj:
    """Something standing on a tile: a resource or a building."""
    __slots__ = ("id", "kind", "x", "y", "ripe", "timer", "hp", "owner", "village", "progress", "data")

    def __init__(self, id_: int, kind: str, x: int, y: int, ripe: bool = True, owner: int = -1):
        self.id, self.kind, self.x, self.y = id_, kind, x, y
        self.ripe = ripe          # False = harvested / still growing
        self.timer = 0            # world-time units until ripe again
        self.hp = 100
        self.owner = owner        # NPC that built it (-1 = nature)
        self.village = -1
        self.progress = 100       # construction, percent
        self.data: dict = {}

    @property
    def spec(self):
        return OBJECTS[self.kind]

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "x": self.x, "y": self.y, "ripe": self.ripe,
                "hp": self.hp, "owner": self.owner, "village": self.village, "progress": self.progress}


class Actions(dict):
    """Each living NPC's valid actions, worked out the first time they are asked
    for. Finding them means a path search, and most NPCs are in the middle of
    something on any given tick and never ask."""

    def __init__(self, world):
        super().__init__()
        self.world = world

    def __missing__(self, nid):
        n = self.world.by_id.get(nid)
        if n is None or not n.alive:
            raise KeyError(nid)
        acts = self[nid] = self.world._generate(self.world, n)
        return acts

    def get(self, nid, default=None):
        try:
            return self[nid]
        except KeyError:
            return default

    def all(self) -> dict:
        """Everyone's actions, as a plain dict."""
        return {n.id: self[n.id] for n in self.world.npcs}

    def items(self):
        return self.all().items()

    def values(self):
        return self.all().values()

    def keys(self):
        return self.all().keys()

    def __iter__(self):
        return iter(self.all())

    def __len__(self):
        return len(self.world.npcs)

    def __eq__(self, other):
        return self.all() == (other.all() if isinstance(other, Actions) else other)

    __hash__ = None


def cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


class World:
    def __init__(self, seed: int, width: int, height: int, npc_count: int, decay_every: int = 1,
                 map_name: str = mapgen.DEFAULT_MAP):
        from .actions import generate_actions  # (actions imports this module)
        self._generate = generate_actions

        self.rng = Rng(seed)
        # Survival stats, ageing and regrowth advance once per `decay_every` ticks
        # ("world time"), so actions can last several ticks without starving NPCs.
        self.decay_every = max(1, decay_every)
        self.w, self.h = width, height
        self.map_name = map_name
        self.start = (width // 2, height // 2)        # where the founders settle (see mapgen.settle)
        self.tick = 0
        self.rev = 0                                  # bumped whenever a tile changes
        self.trev = 0                                 # bumped whenever the terrain itself changes
        size = width * height
        self.terrain: list[str] = ["dirt"] * size
        self.objs: dict[int, Obj] = {}                # tile index -> object
        self.glyphs: list[str] = [TERRAIN["dirt"].glyph] * size
        self.walk: list[bool] = [True] * size         # can be stood on / walked through
        self.count: Counter = Counter()               # glyph -> how many tiles show it
        self.count[TERRAIN["dirt"].glyph] = size
        # for path-finding: per tile, the kinds of place it is next to (bits of GOTO_BIT),
        # and where each such kind stands
        self.near: list[int] = [0] * size
        self.where: dict[str, set[int]] = {g: set() for g in GOTO_BIT}
        self._rows: tuple[int, list[str]] = (-1, [])  # (rev, the tile view) and the same for terrain
        self._terrain_rows: tuple[int, list[str]] = (-1, [])
        self.timers: set[int] = set()                 # tile indices of objects that are regrowing
        # things lying on the ground: tile index -> {"items": {item: n}, "by": npc id, "ttl": units}
        self.drops: dict[int, dict] = {}
        self.next_obj = 1

        self.npcs: list[Npc] = []                     # the living
        self.by_id: dict[int, Npc] = {}               # the living, plus this step's dead
        self.occ: Counter = Counter()                 # tile index -> NPCs standing there
        self.died: list[Npc] = []                     # died since the last step began
        self.controlled: set[int] = set()             # NPCs the player is steering (they get "step" actions)
        self.graveyard: list[dict] = []               # everyone who ever died
        self.next_id = 1
        self.actions: Actions = Actions(self)
        self.events: list[dict] = []
        self._carry = False                           # events/deaths from apply() await the next step

        mapgen.generate(self, map_name)
        mapgen.settle(self, npc_count)
        self.generate_all_actions()

    # --- tiles ----------------------------------------------------------------
    def inb(self, x, y) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def idx(self, x, y) -> int:
        return y * self.w + x

    def tile(self, x, y) -> str:
        """The legacy one-character view of a tile."""
        return self.glyphs[y * self.w + x]

    def obj_at(self, x, y) -> Obj | None:
        return self.objs.get(y * self.w + x)

    def passable(self, x, y) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h and self.walk[y * self.w + x]

    def occupied(self, x, y) -> bool:
        return self.occ[y * self.w + x] > 0

    def free(self, x, y) -> bool:
        """Open, buildable ground with nothing and nobody on it."""
        if not self.inb(x, y):
            return False
        i = y * self.w + x
        return i not in self.objs and self.occ[i] == 0 and TERRAIN[self.terrain[i]].buildable \
            and TERRAIN[self.terrain[i]].passable

    def _look(self, i: int) -> tuple[str, bool]:
        """What a tile shows and whether it can be walked on, from its terrain and object."""
        t = TERRAIN[self.terrain[i]]
        o = self.objs.get(i)
        if o is None:
            return t.glyph, t.passable
        s = o.spec
        return (s.glyph if o.ripe or not s.spent_glyph else s.spent_glyph,
                t.passable and not (s.blocks if o.ripe else s.spent_blocks))

    def _neighbours(self, i: int):
        """The tiles N, E, S and W of a tile (those that exist)."""
        w, x = self.w, i % self.w
        if i >= w:
            yield i - w
        if x != w - 1:
            yield i + 1
        if i + w < len(self.glyphs):
            yield i + w
        if x:
            yield i - 1

    def _near_of(self, i: int) -> int:
        m = 0
        for j in self._neighbours(i):
            m |= GOTO_BIT.get(self.glyphs[j], 0)
        return m

    def _refresh(self, i: int):
        """Recompute the cached glyph and walkability of one tile."""
        glyph, walk = self._look(i)
        old = self.glyphs[i]
        if old != glyph:
            self.count[old] -= 1
            self.count[glyph] += 1
            self.glyphs[i] = glyph
            if old in GOTO_BIT or glyph in GOTO_BIT:  # the tiles around it are next to something else now
                if old in GOTO_BIT:
                    self.where[old].discard(i)
                if glyph in GOTO_BIT:
                    self.where[glyph].add(i)
                for j in self._neighbours(i):
                    self.near[j] = self._near_of(j)
        self.walk[i] = walk
        self.rev += 1

    def rebuild(self):
        """Recompute every cached per-tile value from scratch (map generation
        writes ``terrain`` directly, a whole map at a time, then calls this)."""
        size = self.w * self.h
        self.count = Counter()
        self.where = {g: set() for g in GOTO_BIT}
        for i in range(size):
            glyph, self.walk[i] = self._look(i)
            self.glyphs[i] = glyph
            self.count[glyph] += 1
            if glyph in GOTO_BIT:
                self.where[glyph].add(i)
        for i in range(size):
            self.near[i] = self._near_of(i)
        self.rev += 1
        self.trev += 1

    def set_terrain(self, x, y, key: str):
        i = self.idx(x, y)
        self.terrain[i] = key
        self.trev += 1
        self._refresh(i)

    def fertility(self, x: int, y: int) -> float:
        """How fast things grow at a tile: the ground itself, and better near water."""
        w, h = self.w, self.h
        f = TERRAIN[self.terrain[y * w + x]].fertile
        if f <= 0:
            return 0.0
        for yy in range(max(0, y - WET_REACH), min(h, y + WET_REACH + 1)):
            row = yy * w
            for xx in range(max(0, x - WET_REACH), min(w, x + WET_REACH + 1)):
                if self.terrain[row + xx] in WATER:
                    return f * WET_BONUS
        return f

    def grow_time(self, o: Obj) -> int:
        """World-time units for an object to ripen where it stands."""
        return max(1, round(o.spec.regrow / max(MIN_FERTILITY, self.fertility(o.x, o.y))))

    def place(self, kind: str, x: int, y: int, ripe: bool | None = None, owner: int = -1) -> Obj:
        """Put an object on a tile, replacing whatever stood there."""
        spec = OBJECTS[kind]
        i = self.idx(x, y)
        self.timers.discard(i)
        o = Obj(self.next_obj, kind, x, y, (not spec.starts_spent) if ripe is None else ripe, owner)
        self.next_obj += 1
        self.objs[i] = o
        if not o.ripe and spec.regrow:
            o.timer = self.grow_time(o)
            self.timers.add(i)
        self._refresh(i)
        return o

    def remove(self, x: int, y: int) -> Obj | None:
        i = self.idx(x, y)
        o = self.objs.pop(i, None)
        if o is not None:
            self.timers.discard(i)
            self._refresh(i)
        return o

    def harvest(self, o: Obj):
        """Mark an object as harvested; it ripens again after its regrow time."""
        i = self.idx(o.x, o.y)
        o.ripe = False
        o.timer = self.grow_time(o) if o.spec.regrow else 0
        if o.timer:
            self.timers.add(i)
        self._refresh(i)

    def adjacent_objs(self, n: Npc):
        """Objects on the four tiles next to an NPC, in N, E, S, W order."""
        w, x, y = self.w, n.x, n.y
        out = []
        if y > 0 and (o := self.objs.get((y - 1) * w + x)):
            out.append(o)
        if x < self.w - 1 and (o := self.objs.get(y * w + x + 1)):
            out.append(o)
        if y < self.h - 1 and (o := self.objs.get((y + 1) * w + x)):
            out.append(o)
        if x > 0 and (o := self.objs.get(y * w + x - 1)):
            out.append(o)
        return out

    # --- things on the ground ---------------------------------------------------
    def drop(self, x: int, y: int, items: dict, by: int = -1):
        """Leave items on a tile (on top of whatever already lies there)."""
        items = {k: n for k, n in items.items() if n > 0}
        if not items:
            return
        pile = self.drops.setdefault(self.idx(x, y), {"items": {}, "by": by, "ttl": DROP_TTL})
        for k, n in items.items():
            pile["items"][k] = pile["items"].get(k, 0) + n
        pile["by"], pile["ttl"] = by, DROP_TTL
        self.events.append({"type": "drop", "npc": by, "x": x, "y": y, "items": items})

    def pile_at(self, x: int, y: int) -> dict | None:
        return self.drops.get(y * self.w + x)

    def pickup(self, n: Npc) -> dict:
        """The NPC takes what it can carry of whatever lies at its feet."""
        i = self.idx(n.x, n.y)
        pile = self.drops.get(i)
        if pile is None:
            return {}
        got = {}
        for item in list(pile["items"]):
            left = n.take(item, pile["items"][item])
            if left < pile["items"][item]:
                got[item] = pile["items"][item] - left
            if left:
                pile["items"][item] = left
            else:
                del pile["items"][item]
        if got:
            self.events.append({"type": "pickup", "npc": n.id, "x": n.x, "y": n.y, "items": got,
                                "owner": pile["by"]})
        if not pile["items"]:
            del self.drops[i]
        return got

    # --- NPCs -----------------------------------------------------------------
    def npc(self, id_) -> Npc | None:
        return self.by_id.get(id_)

    def alive(self) -> list[Npc]:
        return self.npcs

    def spawn(self, x, y, health, hunger, energy, social, age) -> Npc:
        n = Npc(self.next_id, x, y, health, hunger, energy, social, age)
        self.next_id += 1
        self.npcs.append(n)
        self.by_id[n.id] = n
        self.occ[self.idx(x, y)] += 1
        return n

    # --- learning ---------------------------------------------------------------
    def train(self, n: Npc, skill: str, xp: int):
        """Practice makes better: xp toward the next level of a skill, and a
        share of it toward the attributes that skill leans on."""
        spec = SKILLS[skill]
        xp = max(1, round(xp * learn_rate({"attrs": n.attrs})))
        level = n.skills.get(skill, 0)
        if level < SKILL_MAX:
            have = n.skill_xp.get(skill, 0) + xp
            while level < SKILL_MAX and have >= skill_need(level):
                have -= skill_need(level)
                level += 1
                self.events.append({"type": "levelup", "npc": n.id, "skill": skill, "level": level})
            n.skills[skill] = level
            n.skill_xp[skill] = have if level < SKILL_MAX else 0
        for i, attr in enumerate(spec.attrs):
            self.exercise(n, attr, xp if i == 0 else xp // 2)

    def exercise(self, n: Npc, attr: str, xp: int):
        """Using an attribute slowly builds it up."""
        value = n.attrs[attr]
        if value >= ATTR_MAX or xp <= 0:
            return
        have = n.attr_xp.get(attr, 0) + xp
        if have >= attr_need(value):
            have -= attr_need(value)
            n.attrs[attr] = value + 1
            self.events.append({"type": "attrup", "npc": n.id, "attr": attr, "value": value + 1})
        n.attr_xp[attr] = have

    def roll_attrs(self) -> dict[str, int]:
        """A newcomer's attributes: everyone is good at something, bad at something."""
        return {a: 3 + self.rng.below(5) for a in ATTRS}

    def blend_attrs(self, a: Npc, b: Npc) -> dict[str, int]:
        """A child's attributes: between its parents', give or take one."""
        return {k: max(ATTR_MIN, min(ATTR_MAX - 2, (a.attrs[k] + b.attrs[k] + self.rng.below(2)) // 2
                                     + self.rng.below(3) - 1)) for k in ATTRS}

    def move(self, n: Npc, dx: int, dy: int) -> bool:
        if self.passable(n.x + dx, n.y + dy):
            self.occ[self.idx(n.x, n.y)] -= 1
            n.x += dx
            n.y += dy
            self.occ[self.idx(n.x, n.y)] += 1
            n.steps += 1
            if n.steps % WALK_EXERCISE == 0:
                self.exercise(n, "agility", 1)
            if self.drops and self.idx(n.x, n.y) in self.drops:
                self.pickup(n)  # walking over things picks them up
            return True
        return False

    def teleport(self, n: Npc, x: int, y: int):
        self.occ[self.idx(n.x, n.y)] -= 1
        n.x, n.y = x, y
        self.occ[self.idx(x, y)] += 1

    def kill(self, n: Npc, cause: str, killer: int = -1):
        if not n.alive:
            return
        n.alive = False
        n.health = 0
        n.cause = cause
        self.occ[self.idx(n.x, n.y)] -= 1
        self.died.append(n)
        self.graveyard.append({"id": n.id, "x": n.x, "y": n.y, "cause": cause, "killer": killer,
                               "tick": self.tick, "age": n.age})
        self.events.append({"type": "death", "npc": n.id, "cause": cause, "killer": killer})
        if n.inv:  # what they carried stays where they fell, for anyone to take
            self.drop(n.x, n.y, n.inv, n.id)
            n.inv = {}

    def fail(self, n: Npc, kind: str):
        self.events.append({"type": "fail", "npc": n.id, "kind": kind})

    def _bury(self):
        """Drop the dead from the active list (their record stays in the graveyard)."""
        if any(not n.alive for n in self.npcs):
            self.npcs = [n for n in self.npcs if n.alive]
            self.controlled &= {n.id for n in self.npcs}

    # --- time -----------------------------------------------------------------
    def generate_all_actions(self):
        """Forget the old action lists; new ones are made as they are asked for."""
        self.actions = Actions(self)

    def _begin(self):
        """Start collecting a new batch of events (unless apply() already did)."""
        if self._carry:
            return
        self.events = []
        for n in self.died:
            self.by_id.pop(n.id, None)
        self.died = []

    def step(self, choices: dict[int, tuple[int, int]]):
        from .actions import execute

        self._begin()
        self._carry = False
        self.tick += 1
        for n in self.npcs:
            n.asleep = False

        order = [n.id for n in self.npcs]
        for i in range(len(order) - 1, 0, -1):
            j = self.rng.below(i + 1)
            order[i], order[j] = order[j], order[i]

        for nid in order:
            n = self.by_id[nid]
            if not n.alive or nid not in choices:
                continue
            idx, opt = choices[nid]
            acts = self.actions.get(nid, [])
            if not 0 <= idx < len(acts):
                self.fail(n, "invalid")
                continue
            execute(self, n, acts[idx], opt)

        if self.tick % self.decay_every == 0:
            self.world_time(self.tick // self.decay_every)
        self._bury()
        self.generate_all_actions()

    def world_time(self, phase: int):
        """Passive decay, ageing and regrowth: one step of world time."""
        for n in list(self.npcs):
            if not n.alive:
                continue
            n.hunger = max(0, n.hunger - 1)
            n.social = min(STAT_MAX, n.social + 1)
            n.age += 1
            n.rest = max(0, n.rest - 1)
            if phase % 2 == 0:
                n.energy = max(0, n.energy - 1)
            if n.hunger == 0 or n.energy == 0:
                if n.hunger == 0:
                    n.health = max(0, n.health - 3)
                if n.energy == 0:
                    n.health = max(0, n.health - 2)
                if n.health <= 0:
                    self.kill(n, "starvation" if n.hunger == 0 else "exhaustion")
            elif n.hunger >= 50 and n.energy >= 30 and n.health < STAT_MAX:
                n.health += 1
            if n.alive and n.age >= ELDER_AGE and self.rng.below(OLD_AGE_ODDS) < n.age - ELDER_AGE:
                self.kill(n, "old age")
            if n.alive and n.expecting:
                n.expecting -= 1
                if n.expecting == 0:
                    child = self.spawn(n.x, n.y, 60, 70, 70, 0, 0)
                    if n.child_attrs:
                        child.attrs = n.child_attrs
                    n.child_attrs = None
                    self.events.append({"type": "birth", "npc": child.id, "parents": [n.id, n.partner]})
                    n.partner = -1

        for i in sorted(self.timers):
            o = self.objs[i]
            o.timer -= 1
            if o.timer <= 0:
                if self.occ[i] > 0 or i in self.drops:
                    o.timer = 1  # someone is standing here (or something lies here); retry
                else:
                    o.ripe = True
                    self.timers.discard(i)
                    self._refresh(i)

        for i in sorted(self.drops):
            self.drops[i]["ttl"] -= 1
            if self.drops[i]["ttl"] <= 0:
                del self.drops[i]

    # --- outside intervention ---------------------------------------------------
    def apply(self, cmd: dict) -> bool:
        """Change the world from outside the rules (player powers, events, tests).

        Returns whether the command did anything. Its events are reported with
        the next step. Commands: ``place``/``clear`` an object, ``terrain``,
        ``give`` items, set a ``stat``, ``spawn`` or ``kill`` an NPC, ``move`` one.
        """
        self._begin()
        self._carry = True
        op = cmd.get("op")
        x, y = cmd.get("x", -1), cmd.get("y", -1)
        n = self.by_id.get(cmd.get("npc", -1))
        if n is not None and not n.alive:
            n = None
        ok = False
        if op == "place" and cmd.get("kind") in OBJECTS and self.inb(x, y) and not self.occupied(x, y):
            self.place(cmd["kind"], x, y, cmd.get("ripe"), cmd.get("owner", -1))
            ok = True
        elif op == "clear" and self.inb(x, y):
            ok = self.remove(x, y) is not None
        elif op == "terrain" and cmd.get("kind") in TERRAIN and self.inb(x, y):
            self.set_terrain(x, y, cmd["kind"])
            ok = True
        elif op == "give" and n and cmd.get("item") in ITEMS:
            n.give(cmd["item"], int(cmd.get("n", 1)))
            ok = True
        elif op == "drop" and cmd.get("item") in ITEMS and self.passable(x, y):
            self.drop(x, y, {cmd["item"]: int(cmd.get("n", 1))})
            ok = True
        elif op == "attr" and n and cmd.get("attr") in ATTRS:
            n.attrs[cmd["attr"]] = max(ATTR_MIN, min(ATTR_MAX, int(cmd.get("value", 5))))
            ok = True
        elif op == "train" and n and cmd.get("skill") in SKILLS:
            self.train(n, cmd["skill"], int(cmd.get("xp", 10)))
            ok = True
        elif op == "stat" and n and cmd.get("stat") in ("health", "hunger", "energy", "social", "age", "rest"):
            v = int(cmd.get("value", 0))
            setattr(n, cmd["stat"], v if cmd["stat"] in ("age", "rest") else max(0, min(STAT_MAX, v)))
            ok = True
        elif op == "spawn" and self.passable(x, y):
            born = self.spawn(x, y, 100, 80, 80, 20, int(cmd.get("age", START_AGE)))
            born.attrs = self.roll_attrs()
            self.events.append({"type": "arrive", "npc": born.id, "x": x, "y": y})
            ok = True
        elif op == "kill" and n:
            self.kill(n, str(cmd.get("cause", "an act of god")))
            ok = True
        elif op == "control" and cmd.get("npc", -1) in self.by_id:
            if cmd.get("on", True) and n:
                self.controlled.add(n.id)
            else:
                self.controlled.discard(cmd["npc"])
            ok = True
        elif op == "move" and n and self.passable(x, y):
            self.teleport(n, x, y)
            ok = True
        if ok:
            self._bury()
            self.generate_all_actions()
        return ok

    # --- snapshot ----------------------------------------------------------------
    def rows(self) -> list[str]:
        """The tile view, one string per row: what each tile shows (the object
        on it, or else its terrain)."""
        if self._rows[0] != self.rev:
            g, w = self.glyphs, self.w
            self._rows = (self.rev, ["".join(g[y * w:(y + 1) * w]) for y in range(self.h)])
        return self._rows[1]

    def terrain_rows(self) -> list[str]:
        """The ground alone, one string per row."""
        if self._terrain_rows[0] != self.trev:
            g, w = [TERRAIN[k].glyph for k in self.terrain], self.w
            self._terrain_rows = (self.trev, ["".join(g[y * w:(y + 1) * w]) for y in range(self.h)])
        return self._terrain_rows[1]

    def state(self) -> dict:
        return {
            "tick": self.tick, "width": self.w, "height": self.h, "rev": self.rev, "trev": self.trev,
            "tiles": self.rows(), "terrain": self.terrain_rows(),
            # the living, then anyone who died since the last step began
            "npcs": [n.to_json() for n in self.npcs] + [n.to_json() for n in self.died],
            "drops": [[i % self.w, i // self.w, dict(self.drops[i]["items"])] for i in sorted(self.drops)],
            "actions": self.actions,
            "events": self.events,
        }
