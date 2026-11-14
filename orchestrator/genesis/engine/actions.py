"""What NPCs can do: which actions the engine offers, and what each one does.

Every action kind in ``defs.ACTIONS`` has two handlers here, registered with
``@offers(kind)`` and ``@runs(kind)``. Interaction options (``defs.INTERACTIONS``)
have an ``@option(key)`` handler pair. Missing handlers fail at import time.

A brain never invents an action: it picks an index into the list
``generate_actions`` produced, and ``execute`` re-checks it before acting.
"""
from __future__ import annotations

from .defs import (ACTIONS, ATTR_AVERAGE, BUILDINGS, COST_ATTACK, COST_DEFEND, COST_MATE, DIRS, EAT_HUNGER, ELDER_AGE,
                   GESTATION, GOTO_BIT, GOTO_LIMIT, INTERACTIONS, ITEMS, MATE_COOLDOWN, MATE_PRIVACY, MATE_SOCIAL,
                   MATURE_AGE, MAX_TARGETS, OBJECTS, POP_CAP, RECIPES, SIGHT, SLEEP_ENERGY, STAT_MAX, TALK_SOCIAL,
                   TERRAIN, can_afford, harvest_yield, tire_rate, trade_terms)
from .path import build_site, scan
from .world import cheb

OFFER: dict = {}
RUN: dict = {}
OPTION_LIST: dict = {}   # interaction key -> fn(world, a, b) -> the option strings on offer
OPTION_RUN: dict = {}    # interaction key -> fn(world, a, b, *details)


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
    """Register an interaction. ``available(world, a, b)`` says when it is on
    offer: True/False, or a list of detailed options such as "trade:wood:food"."""
    def reg(fn):
        def listing(w, a, b):
            r = available(w, a, b) if available else True
            return ([key] if r else []) if isinstance(r, bool) else r
        OPTION_LIST[key] = listing
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
            n = self.n  # only things on the ground this NPC still has room for are worth a walk
            piles = [i for i, p in self.world.drops.items() if any(n.room(k) > 0 for k in p["items"])]
            self._scan = scan(self.world, n.x, n.y, GOTO_BIT, self.far, GOTO_LIMIT, piles)
        return self._scan

    def step(self, act: dict, dx: int, dy: int) -> dict:
        """Finish a "take one step that way" action: slow ground costs extra time."""
        w, n = self.world, self.n
        cost = TERRAIN[w.terrain[w.idx(n.x + dx, n.y + dy)]].move_cost
        if cost > 1:
            act["cost"] = cost
        return act


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
    """Pay an action's energy cost (the tough pay less)."""
    n.energy = max(0, n.energy - max(1, round(energy * tire_rate({"attrs": n.attrs}))))


def _practice(w, n, kind: str):
    d = ACTIONS[kind]
    if d.skill:
        w.train(n, d.skill, d.xp)


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
        return [{"kind": kind, "what": o.kind, "x": o.x, "y": o.y}] if o else []
    return offer


def _run_harvest(kind):
    def run(w, n, act, opt):
        x, y = act["x"], act["y"]
        o = w.obj_at(x, y) if w.inb(x, y) else None
        if o is None or not o.ripe or o.spec.harvest != kind or abs(x - n.x) + abs(y - n.y) != 1:
            return w.fail(n, kind)
        w.harvest(o)
        gives = harvest_yield(o.spec, n.level(ACTIONS[kind].skill))  # (the practised get more out of it)
        if o.spec.drops and w.passable(x, y):
            w.drop(x, y, gives, n.id)  # it falls where the thing stood; walk over to pick it up
        else:
            spill = {item: n.take(item, amount) for item, amount in gives.items()}
            w.drop(n.x, n.y, spill, n.id)  # whatever the NPC cannot carry
        _spend(n, o.spec.harvest_energy)
        _practice(w, n, kind)
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
    soil = round(w.fertility(*site), 2)  # how well things will grow there
    return [{"kind": "build", "what": b.key, "x": site[0], "y": site[1], "soil": soil} for b in can]


@runs("build")
def _(w, n, act, opt):
    x, y = act["x"], act["y"]
    spec = OBJECTS.get(act["what"])
    if spec is None or not spec.cost or not can_afford(n.inv, spec.cost) or not w.free(x, y):
        return w.fail(n, "build")
    o = w.place(spec.key, x, y, owner=n.id)
    n.pay(spec.cost)
    _spend(n, spec.build_energy)
    w.train(n, "building", spec.build_ticks)  # (bigger jobs teach more)
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
    _practice(w, n, "craft")
    w.events.append({"type": "craft", "npc": n.id, "what": r.key})


# --- picking things up -----------------------------------------------------------------
@offers("collect")
def _(w, n, ctx):
    r = ctx.scan.get("cell")
    if not r:
        return []
    dist, dx, dy, x, y = r
    pile = w.pile_at(x, y)
    by = w.npc(pile["by"])
    # whose it is: whoever left it there, while they live (-1 = nobody's)
    owner = by.id if by is not None and by.alive else -1
    return [ctx.step({"kind": "collect", "x": x, "y": y, "dist": dist, "dx": dx, "dy": dy,
                      "items": dict(pile["items"]), "owner": owner}, dx, dy)]


@runs("collect")
def _(w, n, act, opt):
    if w.pile_at(n.x, n.y):
        if not w.pickup(n):
            w.fail(n, "collect")
    elif not w.move(n, act["dx"], act["dy"]):  # (arriving on the tile picks it up)
        w.fail(n, "collect")


# --- moving ------------------------------------------------------------------------
@offers("goto")
def _(w, n, ctx):
    out = []
    for o in OBJECTS.values():
        r = ctx.scan.get(o.glyph) if o.goto else None
        if r and r[0] > 0:
            out.append(ctx.step({"kind": "goto", "what": o.key, "dist": r[0], "dx": r[1], "dy": r[2]}, r[1], r[2]))
    return out


@offers("goto_npc")
def _(w, n, ctx):
    out = []
    for o in ctx.far:
        r = ctx.scan.get(o.id)
        if r and r[0] > 0:
            out.append(ctx.step({"kind": "goto_npc", "target": o.id, "dist": r[0], "dx": r[1], "dy": r[2]},
                                r[1], r[2]))
    return out


def _run_step(w, n, act, opt):
    if not w.move(n, act["dx"], act["dy"]):
        w.fail(n, act["kind"])


runs("goto")(_run_step)
runs("goto_npc")(_run_step)
runs("step")(_run_step)


@offers("step")
def _(w, n, ctx):
    if n.id not in w.controlled:
        return []
    return [ctx.step({"kind": "step", "dx": dx, "dy": dy}, dx, dy) for dx, dy in DIRS if w.passable(n.x + dx, n.y + dy)]


@offers("wander")
def _(w, n, ctx):
    if not any(w.passable(n.x + dx, n.y + dy) for dx, dy in DIRS):
        return []
    return [ctx.step({"kind": "wander"}, 0, 0)]  # (which way is decided on the move: slow if they stand in water)


@runs("wander")
def _(w, n, act, opt):
    opts = [(dx, dy) for dx, dy in DIRS if w.passable(n.x + dx, n.y + dy)]
    if not opts:
        return w.fail(n, "wander")
    dx, dy = opts[w.rng.below(len(opts))]
    w.move(n, dx, dy)


# --- other people --------------------------------------------------------------------
def interact_options(w, a, b) -> list[str]:
    """Everything ``a`` may do with ``b`` right now. Some options carry details:
    "trade:wood:food" (give wood, get food), "give:food"."""
    out: list[str] = []
    for key in INTERACTIONS:
        out.extend(OPTION_LIST[key](w, a, b))
    return out


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
    if choice not in interact_options(w, n, t):
        return w.fail(n, "interact")
    key, *details = choice.split(":")
    OPTION_RUN[key](w, n, t, *details)
    if n.alive:
        w.train(n, INTERACTIONS[key].skill, INTERACTIONS[key].xp)


@option("talk")
def _(w, a, b):
    a.social = max(0, a.social - TALK_SOCIAL)
    b.social = max(0, b.social - TALK_SOCIAL)
    w.events.append({"type": "talk", "npc": a.id, "target": b.id})


def _hit_bonus(n) -> int:
    """Extra damage: a weapon in hand, a strong arm, a practised fighter."""
    return sum(ITEMS[k].damage for k in n.inv) + (n.attrs["strength"] - ATTR_AVERAGE) + n.level("combat") // 2


def hit_chance(a, b) -> int:
    """Percent chance that ``a`` lands a blow on ``b``."""
    return max(40, min(95, 75 + 2 * a.level("combat") - 2 * (b.attrs["agility"] - ATTR_AVERAGE)))


@option("fight")
def attack(w, a, b):
    _spend(a, COST_ATTACK)
    hit = w.rng.below(100) < hit_chance(a, b)
    dmg = 0
    if hit:
        dmg = max(1, 8 + w.rng.below(8) + _hit_bonus(a))
        b.health = max(0, b.health - dmg)
    w.events.append({"type": "attack", "npc": a.id, "target": b.id, "hit": hit,
                     "damage": dmg, "retaliation": False})
    if b.health <= 0:
        w.kill(b, "combat", a.id)  # (what they carried falls to the ground)
        return
    if w.rng.below(100) < 50:
        rdmg = max(1, 5 + w.rng.below(6) + _hit_bonus(b))
        w.train(b, "combat", 5)  # fighting back is practice too
        a.health = max(0, a.health - rdmg)
        _spend(b, COST_DEFEND)
        w.events.append({"type": "attack", "npc": b.id, "target": a.id, "hit": True,
                         "damage": rdmg, "retaliation": True})
        if a.health <= 0:
            w.kill(a, "combat", b.id)


def trade_ok(w, a, b, give: str, get: str) -> bool:
    """Can ``a`` hand over ``give`` for ``b``'s ``get`` at the going rate?"""
    if give == get or give not in ITEMS or get not in ITEMS:
        return False
    n, m = trade_terms(give, get)
    return a.have(give) >= n and b.have(get) >= m and a.room(get) >= m and b.room(give) >= n


def trades(w, a, b) -> list[str]:
    return [f"trade:{give}:{get}" for give in ITEMS for get in ITEMS if trade_ok(w, a, b, give, get)]


@option("trade", trades)
def _(w, a, b, give, get):
    n, m = trade_terms(give, get)
    a.give(give, -n); b.give(give, n)
    b.give(get, -m); a.give(get, m)
    w.events.append({"type": "trade", "npc": a.id, "target": b.id, "gave": give, "gave_n": n,
                     "got": get, "got_n": m})


def gifts(w, a, b) -> list[str]:
    return [f"give:{k}" for k, d in ITEMS.items() if a.have(k) >= d.gift and b.room(k) >= d.gift]


@option("give", gifts)
def _(w, a, b, item):
    n = ITEMS[item].gift
    a.give(item, -n)
    b.give(item, n)
    w.events.append({"type": "gift", "npc": a.id, "target": b.id, "item": item, "n": n})


def mate_ok(w, a, b) -> bool:
    if a.age < MATURE_AGE or b.age < MATURE_AGE or a.age >= ELDER_AGE or b.age >= ELDER_AGE:
        return False
    if a.social < MATE_SOCIAL or b.social < MATE_SOCIAL or a.rest > 0 or b.rest > 0:
        return False
    if a.expecting or b.expecting:
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
    # the child arrives later (see World.world_time); the one who asked carries it
    a.expecting = GESTATION
    a.partner = b.id
    a.child_attrs = w.blend_attrs(a, b)
    w.events.append({"type": "conceive", "npc": a.id, "target": b.id})


# --- every declared action and option must be implemented -----------------------------
_missing = [k for k in ACTIONS if k not in OFFER or k not in RUN] + [k for k in INTERACTIONS if k not in OPTION_RUN]
assert not _missing, f"defs entries without an engine handler: {_missing}"
assert next(iter(ACTIONS)) == "sleep", "sleep must be the first action offered"
