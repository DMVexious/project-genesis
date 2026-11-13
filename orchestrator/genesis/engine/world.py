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
from .defs import (ELDER_AGE, ITEMS, OBJECTS, OLD_AGE_ODDS, STAT_MAX, TERRAIN)
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
        self.tick = 0
        self.rev = 0                                  # bumped whenever a tile changes
        size = width * height
        self.terrain: list[str] = ["dirt"] * size
        self.objs: dict[int, Obj] = {}                # tile index -> object
        self.glyphs: list[str] = [TERRAIN["dirt"].glyph] * size
        self.walk: list[bool] = [True] * size         # can be stood on / walked through
        self.count: Counter = Counter()               # glyph -> how many tiles show it
        self.count[TERRAIN["dirt"].glyph] = size
        self.timers: set[int] = set()                 # tile indices of objects that are regrowing
        self.next_obj = 1

        self.npcs: list[Npc] = []                     # the living
        self.by_id: dict[int, Npc] = {}               # the living, plus this step's dead
        self.occ: Counter = Counter()                 # tile index -> NPCs standing there
        self.died: list[Npc] = []                     # died since the last step began
        self.graveyard: list[dict] = []               # everyone who ever died
        self.next_id = 1
        self.actions: dict[int, list[dict]] = {}
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

    def _refresh(self, i: int):
        """Recompute the cached glyph and walkability of one tile."""
        t = TERRAIN[self.terrain[i]]
        o = self.objs.get(i)
        if o is None:
            glyph, walk = t.glyph, t.passable
        else:
            s = o.spec
            glyph = s.glyph if o.ripe or not s.spent_glyph else s.spent_glyph
            walk = t.passable and not (s.blocks if o.ripe else s.spent_blocks)
        old = self.glyphs[i]
        if old != glyph:
            self.count[old] -= 1
            self.count[glyph] += 1
            self.glyphs[i] = glyph
        self.walk[i] = walk
        self.rev += 1

    def set_terrain(self, x, y, key: str):
        i = self.idx(x, y)
        self.terrain[i] = key
        self._refresh(i)

    def place(self, kind: str, x: int, y: int, ripe: bool | None = None, owner: int = -1) -> Obj:
        """Put an object on a tile, replacing whatever stood there."""
        spec = OBJECTS[kind]
        i = self.idx(x, y)
        self.timers.discard(i)
        o = Obj(self.next_obj, kind, x, y, (not spec.starts_spent) if ripe is None else ripe, owner)
        self.next_obj += 1
        self.objs[i] = o
        if not o.ripe and spec.regrow:
            o.timer = spec.regrow
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
        o.timer = o.spec.regrow
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

    def move(self, n: Npc, dx: int, dy: int) -> bool:
        if self.passable(n.x + dx, n.y + dy):
            self.occ[self.idx(n.x, n.y)] -= 1
            n.x += dx
            n.y += dy
            self.occ[self.idx(n.x, n.y)] += 1
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

    def fail(self, n: Npc, kind: str):
        self.events.append({"type": "fail", "npc": n.id, "kind": kind})

    def _bury(self):
        """Drop the dead from the active list (their record stays in the graveyard)."""
        if any(not n.alive for n in self.npcs):
            self.npcs = [n for n in self.npcs if n.alive]

    # --- time -----------------------------------------------------------------
    def generate_all_actions(self):
        self.actions = {n.id: self._generate(self, n) for n in self.npcs}

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

        for i in sorted(self.timers):
            o = self.objs[i]
            o.timer -= 1
            if o.timer <= 0:
                if self.occ[i] > 0:
                    o.timer = 1  # someone is standing here; retry
                else:
                    o.ripe = True
                    self.timers.discard(i)
                    self._refresh(i)

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
        elif op == "stat" and n and cmd.get("stat") in ("health", "hunger", "energy", "social", "age", "rest"):
            v = int(cmd.get("value", 0))
            setattr(n, cmd["stat"], v if cmd["stat"] in ("age", "rest") else max(0, min(STAT_MAX, v)))
            ok = True
        elif op == "spawn" and self.passable(x, y):
            born = self.spawn(x, y, 100, 80, 80, 20, int(cmd.get("age", 100)))
            self.events.append({"type": "arrive", "npc": born.id, "x": x, "y": y})
            ok = True
        elif op == "kill" and n:
            self.kill(n, str(cmd.get("cause", "an act of god")))
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
        g, w = self.glyphs, self.w
        return ["".join(g[y * w:(y + 1) * w]) for y in range(self.h)]

    def state(self) -> dict:
        return {
            "tick": self.tick, "width": self.w, "height": self.h, "rev": self.rev,
            "tiles": self.rows(),
            # the living, then anyone who died since the last step began
            "npcs": [n.to_json() for n in self.npcs] + [n.to_json() for n in self.died],
            "actions": self.actions,
            "events": self.events,
        }
