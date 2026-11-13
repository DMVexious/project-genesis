"""What NPCs can do: which actions the engine offers, and what each one does.

Every action kind in ``defs.ACTIONS`` has two handlers here, registered with
``@offers(kind)`` and ``@runs(kind)``. Interaction options (``defs.INTERACTIONS``)
have an ``@option(key)`` handler pair. Missing handlers fail at import time.

A brain never invents an action: it picks an index into the list
``generate_actions`` produced, and ``execute`` re-checks it before acting.
"""
from __future__ import annotations

from .defs import (ACTIONS, BUILDINGS, COST_ATTACK, COST_DEFEND, COST_MATE, DIRS, EAT_HUNGER, ELDER_AGE,
                   GOTO_LIMIT, INTERACTIONS, ITEMS, MATE_COOLDOWN, MATE_PRIVACY, MATE_SOCIAL, MATURE_AGE,
                   MAX_TARGETS, OBJECTS, POP_CAP, RECIPES, SIGHT, SLEEP_ENERGY, STAT_MAX, TALK_SOCIAL,
                   can_afford)
from .path import build_site, scan
from .world import cheb

OFFER: dict = {}
RUN: dict = {}
OPTION_OK: dict = {}
OPTION_RUN: dict = {}


def offers(kind):
    def reg(fn):
        OFFER[kind] = fn
        return fn
    return reg


def runs(kind):
    def reg(fn):
        RUN[kind] = fn
        return fn
    return reg


def option(key, available=None):
    def reg(fn):
        OPTION_OK[key] = available or (lambda w, a, b: True)
        OPTION_RUN[key] = fn
        return fn
    return reg


class Context:
    """Things several offers need about one NPC, computed at most once."""

    def __init__(self, world, n):
        self.world, self.n = world, n
        self._scan = None
        self.others = [o for o in world.npcs if o.id != n.id]
        far = [o for o in self.others if cheb(o.x, o.y, n.x, n.y) > 1
               and abs(o.x - n.x) + abs(o.y - n.y) <= SIGHT]
        far.sort(key=lambda o: (abs(o.x - n.x) + abs(o.y - n.y), o.id))
        self.far = far[:MAX_TARGETS]

    @property
    def scan(self) -> dict:
        if self._scan is None:
            glyphs = [o.glyph for o in OBJECTS.values() if o.goto]
            self._scan = scan(self.world, self.n.x, self.n.y, glyphs, self.far, GOTO_LIMIT)
        return self._scan


def generate_actions(world, n) -> list[dict]:
    if n.energy == 0:
        return [{"kind": "sleep"}]
    ctx = Context(world, n)
    acts: list[dict] = []
    for kind in ACTIONS:
        acts.extend(OFFER[kind](world, n, ctx))
    return acts


def execute(world, n, act: dict, opt: int):
    RUN[act["kind"]](world, n, act, opt)


def _spend(n, energy: int):
    n.energy = max(0, n.energy - energy)


# --- rest and food -------------------------------------------------------------
@offers("sleep")
def _(w, n, ctx):
    return [{"kind": "sleep"}]


@runs("sleep")
def _(w, n, act, opt):
    near = [o.spec for o in w.adjacent_objs(n)]
    gain = max([SLEEP_ENERGY] + [s.sleep_energy for s in near])
    heal = max([0] + [s.sleep_heal for s in near])
    n.energy = min(STAT_MAX, n.energy + gain)
    n.health = min(STAT_MAX, n.health + heal)
    n.asleep = True
    w.events.append({"type": "sleep", "npc": n.id, "hut": any(s.home for s in near)})


@offers("eat")
def _(w, n, ctx):
    return [{"kind": "eat"}] if n.have("food") > 0 else []


@runs("eat")
def _(w, n, act, opt):
    if n.have("food") <= 0:
        return w.fail(n, "eat")
    n.give("food", -1)
    bonus = max([0] + [o.spec.cook_bonus for o in w.adjacent_objs(n)])
    n.hunger = min(STAT_MAX, n.hunger + EAT_HUNGER + bonus)
    w.events.append({"type": "eat", "npc": n.id, "cooked": bonus > 0})


# --- harvesting ------------------------------------------------------------------
def _harvestable(w, n, kind):
    """The best ripe thing next to the NPC that this action harvests."""
    best = None
    for o in w.adjacent_objs(n):
        s = o.spec
        if o.ripe and s.harvest == kind and (best is None or sum(s.gives.values()) > sum(best.spec.gives.values())):
            best = o
    return best


def _offer_harvest(kind):
    def offer(w, n, ctx):
        o = _harvestable(w, n, kind)
        return [{"kind": kind, "x": o.x, "y": o.y}] if o else []
    return offer


def _run_harvest(kind):
    def run(w, n, act, opt):
        x, y = act["x"], act["y"]
        o = w.obj_at(x, y) if w.inb(x, y) else None
        if o is None or not o.ripe or o.spec.harvest != kind or abs(x - n.x) + abs(y - n.y) != 1:
            return w.fail(n, kind)
        w.harvest(o)
        for item, amount in o.spec.gives.items():
            n.give(item, amount)
        _spend(n, o.spec.harvest_energy)
        w.events.append({"type": kind, "npc": n.id, "x": x, "y": y, "what": o.kind})
    return run


for _kind in ("chop", "forage"):
    offers(_kind)(_offer_harvest(_kind))
    runs(_kind)(_run_harvest(_kind))


# --- making things -----------------------------------------------------------------
@offers("build")
def _(w, n, ctx):
    can = [b for b in BUILDINGS if can_afford(n.inv, b.cost)]
    if not can:
        return []
    site = build_site(w, n)
    if not site:
        return []
    return [{"kind": "build", "what": b.key, "x": site[0], "y": site[1]} for b in can]


@runs("build")
def _(w, n, act, opt):
    x, y = act["x"], act["y"]
    spec = OBJECTS.get(act["what"])
    if spec is None or not spec.cost or not can_afford(n.inv, spec.cost) or not w.free(x, y):
        return w.fail(n, "build")
    o = w.place(spec.key, x, y, owner=n.id)
    n.pay(spec.cost)
    _spend(n, spec.build_energy)
    w.events.append({"type": "build", "npc": n.id, "what": spec.key, "x": x, "y": y, "obj": o.id})


def _can_craft(n, r) -> bool:
    cap = ITEMS[r.key].cap
    return can_afford(n.inv, r.cost) and not (cap and n.have(r.key) >= cap)


@offers("craft")
def _(w, n, ctx):
    return [{"kind": "craft", "what": r.key} for r in RECIPES.values() if _can_craft(n, r)]


@runs("craft")
def _(w, n, act, opt):
    r = RECIPES.get(act["what"])
    if r is None or not _can_craft(n, r):
        return w.fail(n, "craft")
    n.pay(r.cost)
    n.give(r.key, 1)
    _spend(n, r.energy)
    w.events.append({"type": "craft", "npc": n.id, "what": r.key})


# --- moving ------------------------------------------------------------------------
@offers("goto")
def _(w, n, ctx):
    out = []
    for o in OBJECTS.values():
        r = ctx.scan.get(o.glyph) if o.goto else None
        if r and r[0] > 0:
            out.append({"kind": "goto", "what": o.key, "dist": r[0], "dx": r[1], "dy": r[2]})
    return out


@offers("goto_npc")
def _(w, n, ctx):
    out = []
    for o in ctx.far:
        r = ctx.scan.get(o.id)
        if r and r[0] > 0:
            out.append({"kind": "goto_npc", "target": o.id, "dist": r[0], "dx": r[1], "dy": r[2]})
    return out


def _run_step(w, n, act, opt):
    if not w.move(n, act["dx"], act["dy"]):
        w.fail(n, act["kind"])


runs("goto")(_run_step)
runs("goto_npc")(_run_step)


@offers("wander")
def _(w, n, ctx):
    return [{"kind": "wander"}] if any(w.passable(n.x + dx, n.y + dy) for dx, dy in DIRS) else []


@runs("wander")
def _(w, n, act, opt):
    opts = [(dx, dy) for dx, dy in DIRS if w.passable(n.x + dx, n.y + dy)]
    if not opts:
        return w.fail(n, "wander")
    dx, dy = opts[w.rng.below(len(opts))]
    w.move(n, dx, dy)


# --- other people --------------------------------------------------------------------
def interact_options(w, a, b) -> list[str]:
    return [key for key in INTERACTIONS if OPTION_OK[key](w, a, b)]


@offers("interact")
def _(w, n, ctx):
    adjacent = [o for o in ctx.others if cheb(o.x, o.y, n.x, n.y) <= 1]
    return [{"kind": "interact", "target": o.id, "options": interact_options(w, n, o)}
            for o in adjacent[:MAX_TARGETS]]


@runs("interact")
def _(w, n, act, opt):
    t = w.npc(act["target"])
    if t is None or not t.alive or cheb(t.x, t.y, n.x, n.y) > 1:
        return w.fail(n, "interact")
    opts = act["options"]
    choice = opts[opt] if 0 <= opt < len(opts) else "talk"
    if choice not in OPTION_RUN or not OPTION_OK[choice](w, n, t):
        return w.fail(n, "interact")
    OPTION_RUN[choice](w, n, t)


@option("talk")
def _(w, a, b):
    a.social = max(0, a.social - TALK_SOCIAL)
    b.social = max(0, b.social - TALK_SOCIAL)
    w.events.append({"type": "talk", "npc": a.id, "target": b.id})


def _hit_bonus(n) -> int:
    return sum(ITEMS[k].damage for k in n.inv)


def _loot(winner, loser):
    for item in [k for k in loser.inv if ITEMS[k].loot]:
        winner.give(item, loser.inv.pop(item))


@option("fight")
def attack(w, a, b):
    _spend(a, COST_ATTACK)
    hit = w.rng.below(100) < 75
    dmg = 0
    if hit:
        dmg = 8 + w.rng.below(8) + _hit_bonus(a)
        b.health = max(0, b.health - dmg)
    w.events.append({"type": "attack", "npc": a.id, "target": b.id, "hit": hit,
                     "damage": dmg, "retaliation": False})
    if b.health <= 0:
        _loot(a, b)
        w.kill(b, "combat", a.id)
        return
    if w.rng.below(100) < 50:
        rdmg = 5 + w.rng.below(6) + _hit_bonus(b)
        a.health = max(0, a.health - rdmg)
        _spend(b, COST_DEFEND)
        w.events.append({"type": "attack", "npc": b.id, "target": a.id, "hit": True,
                         "damage": rdmg, "retaliation": True})
        if a.health <= 0:
            _loot(b, a)
            w.kill(a, "combat", b.id)


def trade_ok(w, a, b) -> bool:
    return (a.have("wood") >= 2 and b.have("food") >= 1) or (a.have("food") >= 1 and b.have("wood") >= 2)


@option("trade", trade_ok)
def _(w, a, b):
    if a.have("wood") >= 2 and b.have("food") >= 1:
        a.give("wood", -2); b.give("wood", 2); b.give("food", -1); a.give("food", 1)
        gave = "wood"
    else:
        a.give("food", -1); b.give("food", 1); b.give("wood", -2); a.give("wood", 2)
        gave = "food"
    w.events.append({"type": "trade", "npc": a.id, "target": b.id, "gave": gave})


def mate_ok(w, a, b) -> bool:
    if a.age < MATURE_AGE or b.age < MATURE_AGE or a.age >= ELDER_AGE or b.age >= ELDER_AGE:
        return False
    if a.social < MATE_SOCIAL or b.social < MATE_SOCIAL or a.rest > 0 or b.rest > 0:
        return False
    if a.hunger < 40 or b.hunger < 40 or a.health < 50 or b.health < 50:
        return False
    if len(w.npcs) >= POP_CAP:
        return False
    for o in w.npcs:
        if o.alive and o.id != a.id and o.id != b.id and cheb(o.x, o.y, a.x, a.y) <= MATE_PRIVACY:
            return False
    return True


@option("mate", mate_ok)
def _(w, a, b):
    for p in (a, b):
        p.hunger = max(0, p.hunger - COST_MATE)
        _spend(p, COST_MATE)
        p.social = 0
        p.rest = MATE_COOLDOWN
    child = w.spawn(a.x, a.y, 60, 70, 70, 0, 0)
    w.events.append({"type": "birth", "npc": child.id, "parents": [a.id, b.id]})


# --- every declared action and option must be implemented -----------------------------
_missing = [k for k in ACTIONS if k not in OFFER or k not in RUN] + [k for k in INTERACTIONS if k not in OPTION_RUN]
assert not _missing, f"defs entries without an engine handler: {_missing}"
assert next(iter(ACTIONS)) == "sleep", "sleep must be the first action offered"
