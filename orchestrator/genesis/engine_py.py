"""Reference implementation of the Genesis game engine.

This module is a line-for-line mirror of ``engine/src/main.cpp``. Both speak the
same text protocol (one command per line in, one JSON object per line out) and
use the same SplitMix64 RNG with the same call order, so for a given seed and
the same sequence of choices they produce identical worlds. The parity test in
``tests/test_genesis.py`` (test_cpp_matches_python) enforces this whenever the C++ binary is built.

Protocol
--------
    INIT <seed> <width> <height> <npc_count> [decay_every=1]   -> state
    STATE                                      -> state
    STEP <id>:<action>[:<option>] ...          -> state (with events)
    QUIT

The LLM never touches this code: it only picks an integer index into the
``actions`` list the engine generated for each NPC on the previous call.
"""
from __future__ import annotations

import json
import sys

MASK64 = (1 << 64) - 1

# --- Tiles -----------------------------------------------------------------
DIRT, ROCK, TREE, STUMP, BUSH, BUSH_EMPTY, HUT = ".", "#", "T", "s", "B", "b", "H"
FARM, FARM_EMPTY, FIRE = "F", "f", "C"  # ripe crops, growing crops, campfire
PASSABLE = (DIRT, STUMP)

# --- Tuning (must match main.cpp) -------------------------------------------
STAT_MAX = 100
CHOP_WOOD = 4
HUT_COST = 10
SPEAR_COST = 5
FORAGE_FOOD = 2
EAT_HUNGER = 30
SLEEP_ENERGY = 20
SLEEP_HUT_ENERGY = 35
SLEEP_HUT_HEAL = 2
COST_CHOP = 5
COST_BUILD = 15
COST_FORAGE = 2
COST_CRAFT = 5
COST_ATTACK = 6
COST_DEFEND = 4
COST_MATE = 15
TREE_REGROW = 60
BUSH_REGROW = 40
MATURE_AGE = 60
START_AGE = 100
POP_CAP = 40
SIGHT = 20
GOTO_LIMIT = 60
BUILD_SEARCH = 6
MAX_TARGETS = 3
TALK_SOCIAL = 5
MATE_SOCIAL = 30
MATE_PRIVACY = 1        # nobody else may be this close when two NPCs mate
FARM_FOOD = 3
FARM_REGROW = 25
COOK_BONUS = 15          # eating next to a campfire
SLEEP_FIRE_ENERGY = 28   # sleeping next to a campfire (a hut beats it)
ELDER_AGE = 700          # from here on, each unit of world time may be the last
OLD_AGE_ODDS = 40000     # death chance per unit = (age - ELDER_AGE) / OLD_AGE_ODDS

# What NPCs can build: (name, tile placed, wood cost, energy cost). A farm is
# planted as growing crops; NPCs choose which one to build, the engine only
# picks the nearest free tile.
BUILDINGS = (("hut", HUT, HUT_COST, COST_BUILD), ("farm", FARM_EMPTY, 6, 8), ("fire", FIRE, 3, 4))
MIN_BUILD_COST = min(b[2] for b in BUILDINGS)

DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))  # N, E, S, W


class Rng:
    """SplitMix64. Identical output to the C++ version."""

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


class Npc:
    __slots__ = ("id", "x", "y", "health", "hunger", "energy", "social", "wood",
                 "food", "spear", "age", "alive", "asleep", "cause")

    def __init__(self, id_, x, y, health, hunger, energy, social, age):
        self.id, self.x, self.y = id_, x, y
        self.health, self.hunger, self.energy, self.social = health, hunger, energy, social
        self.wood = self.food = self.spear = 0
        self.age = age
        self.alive = True
        self.asleep = False
        self.cause = ""

    def to_json(self) -> dict:
        return {
            "id": self.id, "x": self.x, "y": self.y,
            "health": self.health, "hunger": self.hunger, "energy": self.energy,
            "social": self.social, "wood": self.wood, "food": self.food,
            "spear": self.spear, "age": self.age, "alive": self.alive,
            "asleep": self.asleep, "cause": self.cause,
        }


def cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


class World:
    def __init__(self, seed: int, width: int, height: int, npc_count: int, decay_every: int = 1):
        self.rng = Rng(seed)
        # Survival stats, ageing and regrowth advance once per `decay_every` ticks
        # ("world time"), so actions can last several ticks without starving NPCs.
        self.decay_every = max(1, decay_every)
        self.w, self.h = width, height
        self.tick = 0
        self.tiles: list[str] = []
        self.regrow = [0] * (width * height)
        self.npcs: list[Npc] = []
        self.next_id = 1
        self.actions: dict[int, list[dict]] = {}
        self.events: list[dict] = []

        for _ in range(width * height):
            r = self.rng.below(100)
            if r < 7:
                self.tiles.append(ROCK)
            elif r < 13:
                self.tiles.append(TREE)
            elif r < 16:
                self.tiles.append(BUSH)
            else:
                self.tiles.append(DIRT)

        for _ in range(npc_count):
            while True:
                x = self.rng.below(width)
                y = self.rng.below(height)
                if self.tile(x, y) == DIRT and self.npc_at(x, y) is None:
                    break
            hunger = 60 + self.rng.below(41)
            energy = 60 + self.rng.below(41)
            social = self.rng.below(50)
            self.spawn(x, y, 100, hunger, energy, social, START_AGE)

        self.generate_all_actions()

    # --- helpers --------------------------------------------------------
    def inb(self, x, y) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def tile(self, x, y) -> str:
        return self.tiles[y * self.w + x]

    def set_tile(self, x, y, t):
        self.tiles[y * self.w + x] = t

    def passable(self, x, y) -> bool:
        return self.inb(x, y) and self.tile(x, y) in PASSABLE

    def npc(self, id_) -> Npc | None:
        for n in self.npcs:
            if n.id == id_:
                return n
        return None

    def npc_at(self, x, y) -> Npc | None:
        for n in self.npcs:
            if n.alive and n.x == x and n.y == y:
                return n
        return None

    def alive(self) -> list[Npc]:
        return [n for n in self.npcs if n.alive]

    def spawn(self, x, y, health, hunger, energy, social, age) -> Npc:
        n = Npc(self.next_id, x, y, health, hunger, energy, social, age)
        self.next_id += 1
        self.npcs.append(n)
        return n

    def adjacent_tile(self, n: Npc, kind: str):
        for dx, dy in DIRS:
            x, y = n.x + dx, n.y + dy
            if self.inb(x, y) and self.tile(x, y) == kind:
                return x, y
        return None

    def bfs(self, sx, sy, goal, limit):
        """Breadth-first search (uniform-cost Dijkstra) over passable tiles.

        Returns (dist, first_dx, first_dy) for the nearest tile satisfying
        ``goal`` or None. Neighbour order is fixed so results are reproducible.
        """
        if goal(sx, sy):
            return 0, 0, 0
        w = self.w
        seen = [False] * (w * self.h)
        seen[sy * w + sx] = True
        frontier = [(sx, sy, 0, 0)]
        dist = 0
        while frontier and dist < limit:
            dist += 1
            nxt = []
            for x, y, fdx, fdy in frontier:
                for dx, dy in DIRS:
                    nx, ny = x + dx, y + dy
                    if not self.passable(nx, ny) or seen[ny * w + nx]:
                        continue
                    seen[ny * w + nx] = True
                    sdx, sdy = (dx, dy) if dist == 1 else (fdx, fdy)
                    if goal(nx, ny):
                        return dist, sdx, sdy
                    nxt.append((nx, ny, sdx, sdy))
            frontier = nxt
        return None

    def build_site(self, n: Npc):
        """Nearest free dirt tile (by walking distance) for a new hut."""
        w = self.w
        seen = [False] * (w * self.h)
        seen[n.y * w + n.x] = True
        frontier = [(n.x, n.y)]
        for _ in range(BUILD_SEARCH):
            nxt = []
            for x, y in frontier:
                for dx, dy in DIRS:
                    nx, ny = x + dx, y + dy
                    if not self.passable(nx, ny) or seen[ny * w + nx]:
                        continue
                    seen[ny * w + nx] = True
                    if self.tile(nx, ny) == DIRT and self.npc_at(nx, ny) is None:
                        return nx, ny
                    nxt.append((nx, ny))
            frontier = nxt
        return None

    def near_tile_goal(self, kind):
        def goal(x, y):
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if self.inb(nx, ny) and self.tile(nx, ny) == kind:
                    return True
            return False
        return goal

    def mate_ok(self, a: Npc, b: Npc) -> bool:
        if a.age < MATURE_AGE or b.age < MATURE_AGE or a.age >= ELDER_AGE or b.age >= ELDER_AGE:
            return False
        if a.social < MATE_SOCIAL or b.social < MATE_SOCIAL:
            return False
        if a.hunger < 40 or b.hunger < 40 or a.health < 50 or b.health < 50:
            return False
        if len(self.alive()) >= POP_CAP:
            return False
        for o in self.npcs:
            if o.alive and o.id != a.id and o.id != b.id and cheb(o.x, o.y, a.x, a.y) <= MATE_PRIVACY:
                return False
        return True

    def trade_ok(self, a: Npc, b: Npc) -> bool:
        return (a.wood >= 2 and b.food >= 1) or (a.food >= 1 and b.wood >= 2)

    def interact_options(self, a: Npc, b: Npc) -> list[str]:
        opts = ["talk", "fight"]
        if self.trade_ok(a, b):
            opts.append("trade")
        if self.mate_ok(a, b):
            opts.append("mate")
        return opts

    # --- action generation ----------------------------------------------
    def generate_actions(self, n: Npc) -> list[dict]:
        acts: list[dict] = [{"kind": "sleep"}]
        if n.energy == 0:
            return acts
        if n.food > 0:
            acts.append({"kind": "eat"})
        t = self.adjacent_tile(n, TREE)
        if t:
            acts.append({"kind": "chop", "x": t[0], "y": t[1]})
        b = self.adjacent_tile(n, FARM) or self.adjacent_tile(n, BUSH)  # crops first
        if b:
            acts.append({"kind": "forage", "x": b[0], "y": b[1]})
        if n.wood >= MIN_BUILD_COST:
            site = self.build_site(n)
            if site:
                for what, _, cost, _ in BUILDINGS:
                    if n.wood >= cost:
                        acts.append({"kind": "build", "what": what, "x": site[0], "y": site[1]})
        if n.wood >= SPEAR_COST and n.spear == 0:
            acts.append({"kind": "craft_spear"})

        others = [o for o in self.npcs if o.alive and o.id != n.id]
        adjacent = [o for o in others if cheb(o.x, o.y, n.x, n.y) <= 1]
        for o in adjacent[:MAX_TARGETS]:
            acts.append({"kind": "interact", "target": o.id,
                         "options": self.interact_options(n, o)})

        for what, kind in (("tree", TREE), ("bush", BUSH), ("hut", HUT), ("farm", FARM), ("fire", FIRE)):
            r = self.bfs(n.x, n.y, self.near_tile_goal(kind), GOTO_LIMIT)
            if r and r[0] > 0:
                acts.append({"kind": "goto", "what": what, "dist": r[0], "dx": r[1], "dy": r[2]})

        far = [o for o in others if cheb(o.x, o.y, n.x, n.y) > 1
               and abs(o.x - n.x) + abs(o.y - n.y) <= SIGHT]
        far.sort(key=lambda o: (abs(o.x - n.x) + abs(o.y - n.y), o.id))
        for o in far[:MAX_TARGETS]:
            ox, oy = o.x, o.y
            r = self.bfs(n.x, n.y, lambda x, y: cheb(x, y, ox, oy) <= 1, GOTO_LIMIT)
            if r and r[0] > 0:
                acts.append({"kind": "goto_npc", "target": o.id, "dist": r[0], "dx": r[1], "dy": r[2]})

        if any(self.passable(n.x + dx, n.y + dy) for dx, dy in DIRS):
            acts.append({"kind": "wander"})
        return acts

    def generate_all_actions(self):
        self.actions = {n.id: self.generate_actions(n) for n in self.npcs if n.alive}

    # --- execution --------------------------------------------------------
    def kill(self, n: Npc, cause: str, killer: int = -1):
        n.alive = False
        n.health = 0
        n.cause = cause
        self.events.append({"type": "death", "npc": n.id, "cause": cause, "killer": killer})

    def fail(self, n: Npc, kind: str):
        self.events.append({"type": "fail", "npc": n.id, "kind": kind})

    def attack(self, a: Npc, b: Npc):
        a.energy = max(0, a.energy - COST_ATTACK)
        hit = self.rng.below(100) < 75
        dmg = 0
        if hit:
            dmg = 8 + self.rng.below(8) + 12 * a.spear
            b.health = max(0, b.health - dmg)
        self.events.append({"type": "attack", "npc": a.id, "target": b.id, "hit": hit,
                            "damage": dmg, "retaliation": False})
        if b.health <= 0:
            a.wood += b.wood
            a.food += b.food
            b.wood = b.food = 0
            self.kill(b, "combat", a.id)
            return
        if self.rng.below(100) < 50:
            rdmg = 5 + self.rng.below(6) + 12 * b.spear
            a.health = max(0, a.health - rdmg)
            b.energy = max(0, b.energy - COST_DEFEND)
            self.events.append({"type": "attack", "npc": b.id, "target": a.id, "hit": True,
                                "damage": rdmg, "retaliation": True})
            if a.health <= 0:
                b.wood += a.wood
                b.food += a.food
                a.wood = a.food = 0
                self.kill(a, "combat", b.id)

    def move(self, n: Npc, dx: int, dy: int) -> bool:
        if self.passable(n.x + dx, n.y + dy):
            n.x += dx
            n.y += dy
            return True
        return False

    def execute(self, n: Npc, act: dict, opt: int):
        k = act["kind"]
        if k == "sleep":
            near_hut = self.adjacent_tile(n, HUT) is not None
            gain = SLEEP_HUT_ENERGY if near_hut else (
                SLEEP_FIRE_ENERGY if self.adjacent_tile(n, FIRE) else SLEEP_ENERGY)
            n.energy = min(STAT_MAX, n.energy + gain)
            if near_hut:
                n.health = min(STAT_MAX, n.health + SLEEP_HUT_HEAL)
            n.asleep = True
            self.events.append({"type": "sleep", "npc": n.id, "hut": near_hut})
        elif k == "eat":
            if n.food <= 0:
                return self.fail(n, k)
            n.food -= 1
            cooked = self.adjacent_tile(n, FIRE) is not None
            n.hunger = min(STAT_MAX, n.hunger + EAT_HUNGER + (COOK_BONUS if cooked else 0))
            self.events.append({"type": "eat", "npc": n.id, "cooked": cooked})
        elif k == "chop":
            x, y = act["x"], act["y"]
            if self.tile(x, y) != TREE or abs(x - n.x) + abs(y - n.y) != 1:
                return self.fail(n, k)
            self.set_tile(x, y, STUMP)
            self.regrow[y * self.w + x] = TREE_REGROW
            n.wood += CHOP_WOOD
            n.energy = max(0, n.energy - COST_CHOP)
            self.events.append({"type": "chop", "npc": n.id, "x": x, "y": y})
        elif k == "forage":
            x, y = act["x"], act["y"]
            t = self.tile(x, y)
            if t not in (BUSH, FARM) or abs(x - n.x) + abs(y - n.y) != 1:
                return self.fail(n, k)
            farm = t == FARM
            self.set_tile(x, y, FARM_EMPTY if farm else BUSH_EMPTY)
            self.regrow[y * self.w + x] = FARM_REGROW if farm else BUSH_REGROW
            n.food += FARM_FOOD if farm else FORAGE_FOOD
            n.energy = max(0, n.energy - COST_FORAGE)
            self.events.append({"type": "forage", "npc": n.id, "x": x, "y": y})
        elif k == "build":
            x, y = act["x"], act["y"]
            spec = next((b for b in BUILDINGS if b[0] == act["what"]), None)
            if spec is None or n.wood < spec[2] or self.tile(x, y) != DIRT or self.npc_at(x, y) is not None:
                return self.fail(n, k)
            what, tile, cost, energy = spec
            self.set_tile(x, y, tile)
            if tile == FARM_EMPTY:
                self.regrow[y * self.w + x] = FARM_REGROW
            n.wood -= cost
            n.energy = max(0, n.energy - energy)
            self.events.append({"type": "build", "npc": n.id, "what": what, "x": x, "y": y})
        elif k == "craft_spear":
            if n.wood < SPEAR_COST or n.spear:
                return self.fail(n, k)
            n.wood -= SPEAR_COST
            n.spear = 1
            n.energy = max(0, n.energy - COST_CRAFT)
            self.events.append({"type": "craft", "npc": n.id})
        elif k in ("goto", "goto_npc"):
            if not self.move(n, act["dx"], act["dy"]):
                return self.fail(n, k)
        elif k == "wander":
            opts = [(dx, dy) for dx, dy in DIRS if self.passable(n.x + dx, n.y + dy)]
            if not opts:
                return self.fail(n, k)
            dx, dy = opts[self.rng.below(len(opts))]
            self.move(n, dx, dy)
        elif k == "interact":
            t = self.npc(act["target"])
            if t is None or not t.alive or cheb(t.x, t.y, n.x, n.y) > 1:
                return self.fail(n, k)
            opts = act["options"]
            choice = opts[opt] if 0 <= opt < len(opts) else "talk"
            if choice not in self.interact_options(n, t):
                return self.fail(n, k)
            if choice == "talk":
                n.social = max(0, n.social - TALK_SOCIAL)
                t.social = max(0, t.social - TALK_SOCIAL)
                self.events.append({"type": "talk", "npc": n.id, "target": t.id})
            elif choice == "fight":
                self.attack(n, t)
            elif choice == "trade":
                if n.wood >= 2 and t.food >= 1:
                    n.wood -= 2; t.wood += 2; t.food -= 1; n.food += 1
                    gave = "wood"
                else:
                    n.food -= 1; t.food += 1; t.wood -= 2; n.wood += 2
                    gave = "food"
                self.events.append({"type": "trade", "npc": n.id, "target": t.id, "gave": gave})
            elif choice == "mate":
                for p in (n, t):
                    p.hunger = max(0, p.hunger - COST_MATE)
                    p.energy = max(0, p.energy - COST_MATE)
                    p.social = 0
                child = self.spawn(n.x, n.y, 60, 70, 70, 0, 0)
                self.events.append({"type": "birth", "npc": child.id, "parents": [n.id, t.id]})

    def step(self, choices: dict[int, tuple[int, int]]):
        self.tick += 1
        self.events = []
        for n in self.npcs:
            n.asleep = False

        order = [n.id for n in self.npcs if n.alive]
        for i in range(len(order) - 1, 0, -1):
            j = self.rng.below(i + 1)
            order[i], order[j] = order[j], order[i]

        for nid in order:
            n = self.npc(nid)
            if not n.alive or nid not in choices:
                continue
            idx, opt = choices[nid]
            acts = self.actions.get(nid, [])
            if not 0 <= idx < len(acts):
                self.fail(n, "invalid")
                continue
            self.execute(n, acts[idx], opt)

        if self.tick % self.decay_every == 0:
            self.world_time(self.tick // self.decay_every)
        self.generate_all_actions()

    def world_time(self, phase: int):
        """Passive decay, ageing and regrowth: one step of world time."""
        for n in self.npcs:
            if not n.alive:
                continue
            n.hunger = max(0, n.hunger - 1)
            n.social = min(STAT_MAX, n.social + 1)
            n.age += 1
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

        for i in range(self.w * self.h):
            if self.regrow[i] > 0:
                self.regrow[i] -= 1
                if self.regrow[i] == 0:
                    x, y = i % self.w, i // self.w
                    if self.npc_at(x, y) is not None:
                        self.regrow[i] = 1  # someone is standing here; retry
                    elif self.tiles[i] == STUMP:
                        self.tiles[i] = TREE
                    elif self.tiles[i] == BUSH_EMPTY:
                        self.tiles[i] = BUSH
                    elif self.tiles[i] == FARM_EMPTY:
                        self.tiles[i] = FARM

    def state(self) -> dict:
        return {
            "tick": self.tick, "width": self.w, "height": self.h,
            "tiles": ["".join(self.tiles[y * self.w:(y + 1) * self.w]) for y in range(self.h)],
            "npcs": [n.to_json() for n in self.npcs],
            "actions": {str(k): v for k, v in self.actions.items()},
            "events": self.events,
        }


class EngineProcess:
    """Text-protocol front end, identical to the C++ binary's main()."""

    def __init__(self):
        self.world: World | None = None

    def handle(self, line: str) -> str:
        parts = line.split()
        if not parts:
            return json.dumps({"error": "empty command"})
        cmd = parts[0].upper()
        try:
            if cmd == "INIT":
                seed, w, h, n = (int(p) for p in parts[1:5])
                decay = int(parts[5]) if len(parts) > 5 else 1
                self.world = World(seed, w, h, n, decay)
                self.world.events = []
            elif self.world is None:
                return json.dumps({"error": "not initialised"})
            elif cmd == "STATE":
                pass
            elif cmd == "STEP":
                choices = {}
                for tok in parts[1:]:
                    f = [int(v) for v in tok.split(":")]
                    choices[f[0]] = (f[1], f[2] if len(f) > 2 else 0)
                self.world.step(choices)
            else:
                return json.dumps({"error": f"unknown command {cmd}"})
        except (ValueError, IndexError) as e:
            return json.dumps({"error": f"bad command: {e}"})
        return json.dumps(self.world.state(), separators=(",", ":"))


def main():
    proc = EngineProcess()
    for line in sys.stdin:
        if line.strip().upper() == "QUIT":
            break
        sys.stdout.write(proc.handle(line) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
