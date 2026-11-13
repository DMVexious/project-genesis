"""The game's content, in one place.

Terrain, objects (resources and buildings), items, recipes, actions, events
and interaction options are all declared here. The engine, the brains, the
orchestrator, the terminal view and the web front end read these registries
instead of keeping their own copies, so adding content is one entry here plus
the code that makes it do something:

* a new building or resource: an ``ObjectDef`` (its passive effects, cost and
  harvest are data, so most need no code at all);
* a new action: an ``ActionDef`` here, a handler in ``engine/actions.py`` and
  a scorer in ``brain.py`` (both are checked at import time);
* a new interaction option: an ``InteractionDef`` here and a handler in
  ``engine/actions.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# --- Tuning ------------------------------------------------------------------
STAT_MAX = 100
EAT_HUNGER = 30
SLEEP_ENERGY = 20
COST_ATTACK = 6
COST_DEFEND = 4
COST_MATE = 15
MATURE_AGE = 60
START_AGE = 100
POP_CAP = 30
SIGHT = 20
GOTO_LIMIT = 60
BUILD_SEARCH = 6
MAX_TARGETS = 3
TALK_SOCIAL = 5
MATE_SOCIAL = 30
MATE_COOLDOWN = 100      # world-time units a parent rests before another child
MATE_PRIVACY = 1         # nobody else may be this close when two NPCs mate
ELDER_AGE = 700          # from here on, each unit of world time may be the last
OLD_AGE_ODDS = 40000     # death chance per unit = (age - ELDER_AGE) / OLD_AGE_ODDS

DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))  # N, E, S, W


# --- Terrain -----------------------------------------------------------------
@dataclass(frozen=True)
class TerrainDef:
    key: str
    glyph: str                 # one character in the legacy tile view
    label: str
    passable: bool = True
    buildable: bool = True
    fertile: float = 1.0       # crop growth multiplier
    move_cost: int = 1
    term: tuple[str, str] = ("·", "33")  # terminal glyph, ANSI colour


TERRAIN: dict[str, TerrainDef] = {t.key: t for t in (
    TerrainDef("dirt", ".", "bare earth"),
)}


# --- Items -------------------------------------------------------------------
@dataclass(frozen=True)
class ItemDef:
    key: str
    label: str
    emoji: str
    cap: int = 0               # most one NPC can carry (0 = no limit)
    loot: bool = True          # taken by the winner of a fight
    damage: int = 0            # added to every hit while carried


ITEMS: dict[str, ItemDef] = {i.key: i for i in (
    ItemDef("wood", "wood", "🪵"),
    ItemDef("food", "food", "🍒"),
    ItemDef("spear", "spear", "🗡️", cap=1, loot=False, damage=12),
)}


@dataclass(frozen=True)
class RecipeDef:
    key: str                   # the item made
    cost: dict = field(default_factory=dict)
    energy: int = 5
    ticks: int = 6
    why: str = ""


RECIPES: dict[str, RecipeDef] = {r.key: r for r in (
    RecipeDef("spear", {"wood": 5}, why="stronger in fights"),
)}


# --- Objects: resources and buildings ----------------------------------------
@dataclass(frozen=True)
class ObjectDef:
    key: str
    label: str                 # "a hut" (chronicle)
    place: str                 # "berry bush" (prompts)
    glyph: str                 # legacy tile character while ripe / standing
    spent_glyph: str = ""      # ... after it has been harvested
    blocks: bool = True
    spent_blocks: bool = True
    term: tuple[str, str] = ("?", "37")
    spent_term: tuple[str, str] = ("?", "90")
    goto: bool = True          # NPCs are offered "walk to the nearest one"
    # harvesting
    harvest: str = ""          # action kind that harvests it ("chop", "forage")
    gives: dict = field(default_factory=dict)
    harvest_energy: int = 0
    regrow: int = 0            # world-time units until ripe again
    # building (empty cost = NPCs cannot build it)
    cost: dict = field(default_factory=dict)
    build_energy: int = 0
    build_ticks: int = 0
    starts_spent: bool = False
    why: str = ""              # what it is for, shown to the LLM
    emoji: str = "🔨"
    stat: str = ""             # name of its counter in the stats panel
    home: bool = False         # an NPC's first one becomes its home
    # passive effects on an NPC standing next to it
    sleep_energy: int = 0
    sleep_heal: int = 0
    cook_bonus: int = 0

    @property
    def food(self) -> int:
        return self.gives.get("food", 0)


OBJECTS: dict[str, ObjectDef] = {o.key: o for o in (
    ObjectDef("rock", "a rock", "rock", "#", term=("▲", "90"), goto=False),
    ObjectDef("tree", "a tree", "tree", "T", "s", spent_blocks=False,
              term=("♣", "32"), spent_term=("‚", "33"),
              harvest="chop", gives={"wood": 4}, harvest_energy=5, regrow=60),
    ObjectDef("bush", "a berry bush", "berry bush", "B", "b",
              term=("✿", "35"), spent_term=("✽", "90"),
              harvest="forage", gives={"food": 2}, harvest_energy=2, regrow=40),
    ObjectDef("hut", "a hut", "hut", "H", term=("⌂", "93"),
              cost={"wood": 10}, build_energy=15, build_ticks=10,
              why="becomes your home; best sleep and healing", emoji="🔨", stat="huts",
              home=True, sleep_energy=35, sleep_heal=2),
    ObjectDef("farm", "a farm", "farm", "F", "f",
              term=("❦", "92"), spent_term=("ᵥ", "32"),
              harvest="forage", gives={"food": 3}, harvest_energy=2, regrow=25,
              cost={"wood": 6}, build_energy=8, build_ticks=6, starts_spent=True,
              why="crops that regrow 3 food again and again", emoji="🌱", stat="farms"),
    ObjectDef("fire", "a campfire", "campfire", "C", term=("♨", "91"),
              cost={"wood": 3}, build_energy=4, build_ticks=4,
              why="cooked meals fill more, warmer sleep, a gathering spot", emoji="🔥", stat="fires",
              sleep_energy=28, cook_bonus=15),
)}

BUILDINGS: tuple[ObjectDef, ...] = tuple(o for o in OBJECTS.values() if o.cost)


def cost_text(cost: dict) -> str:
    return " + ".join(f"{n} {ITEMS[k].label}" for k, n in cost.items())


def can_afford(inv: dict, cost: dict) -> bool:
    return all(inv.get(k, 0) >= n for k, n in cost.items())


# --- Actions -----------------------------------------------------------------
@dataclass(frozen=True)
class ActionDef:
    kind: str
    ticks: int                               # base duration, before pace and jitter
    describe: Callable[[dict, dict, dict], str]  # (action, names, my stats) -> text for the LLM
    ident: tuple[str, ...] = ()              # fields that tell two actions of this kind apart
    walk: bool = False                       # a multi-tick "keep walking there" goal


def _gain(kind: str) -> str:
    gains = sorted({n for o in OBJECTS.values() if o.harvest == kind for n in o.gives.values()})
    item = next(k for o in OBJECTS.values() if o.harvest == kind for k in o.gives)
    amount = str(gains[0]) if len(gains) == 1 else f"{gains[0]}-{gains[-1]}"
    return f"+{amount} {ITEMS[item].label}"


def _d_build(a, names, me):
    o = OBJECTS[a["what"]]
    return f"Build a {o.place} here (costs {cost_text(o.cost)}; {o.why})"


def _d_craft(a, names, me):
    r = RECIPES[a["what"]]
    return f"Craft a {ITEMS[r.key].label} (costs {cost_text(r.cost)}, {r.why})"


def _name(names, a):
    return names.get(a["target"], "?")


ACTIONS: dict[str, ActionDef] = {a.kind: a for a in (
    # (order here is the order actions are offered in; "sleep" must stay first)
    ActionDef("sleep", 7, lambda a, names, me: "Sleep (restore energy)"),
    ActionDef("eat", 2, lambda a, names, me: f"Eat food (you carry {me['inv'].get('food', 0)})"),
    ActionDef("chop", 5, lambda a, names, me: f"Chop the adjacent tree ({_gain('chop')})"),
    ActionDef("forage", 3, lambda a, names, me: f"Gather food from the adjacent bush or farm ({_gain('forage')})"),
    ActionDef("build", 6, _d_build, ident=("what",)),
    ActionDef("craft", 6, _d_craft, ident=("what",)),
    ActionDef("interact", 4, lambda a, names, me: f"Interact with {_name(names, a)}", ident=("target",)),
    ActionDef("goto", 2, lambda a, names, me:
              f"Walk toward the nearest {OBJECTS[a['what']].place} ({a['dist']} steps)", ident=("what",), walk=True),
    ActionDef("goto_npc", 2, lambda a, names, me:
              f"Walk toward {_name(names, a)} ({a['dist']} steps)", ident=("target",), walk=True),
    ActionDef("wander", 3, lambda a, names, me: "Wander randomly"),
)}


def describe_action(act: dict, names: dict[int, str], me: dict) -> str:
    d = ACTIONS.get(act["kind"])
    return d.describe(act, names, me) if d else act["kind"]


def action_ticks(act: dict) -> int:
    """Base duration of an action in ticks."""
    kind = act["kind"]
    if kind == "build":
        return OBJECTS[act["what"]].build_ticks
    if kind == "craft":
        return RECIPES[act["what"]].ticks
    return ACTIONS[kind].ticks if kind in ACTIONS else 2


def same_action(a: dict, b: dict) -> bool:
    """Whether two offered actions are the same choice (positions may differ)."""
    if a["kind"] != b["kind"]:
        return False
    return all(a.get(f) == b.get(f) for f in ACTIONS[a["kind"]].ident)


# --- Interaction options -------------------------------------------------------
@dataclass(frozen=True)
class InteractionDef:
    key: str
    meaning: str               # shown to the speaker next to the option
    they_want: str             # shown to the listener: "they want ..."
    consent: bool = False      # the other side may refuse
    refused: str = ""          # "they want ..." after the listener refused


INTERACTIONS: dict[str, InteractionDef] = {i.key: i for i in (
    InteractionDef("talk", "just talk", "to talk"),
    InteractionDef("fight", "attack them", "to fight you"),
    InteractionDef("trade", "offer to swap your food and wood", "to trade",
                   consent=True, refused="to trade, and you refused"),
    InteractionDef("mate", "ask them to start a family with you (you are fond of each other; "
                           "a child carries on your line)", "to start a family with you",
                   consent=True, refused="to start a family with you, and you said no"),
)}


# --- Events ------------------------------------------------------------------
# Engine event type -> emoji shown over the NPC. Every type listed is also sent
# to the front end to be animated.
EVENT_EMOJI = {"chop": "🪓", "build": "🔨", "attack": "💢", "birth": "❤️", "forage": "🍒",
               "eat": "🍖", "trade": "🤝", "talk": "💬", "craft": "🗡️", "death": "💀", "sleep": ""}
FX_EVENTS = frozenset(EVENT_EMOJI)


# --- Glyphs (legacy tile view, terminal) -------------------------------------
def _glyphs() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}

    def add(glyph, term, owner):
        assert len(glyph) == 1 and glyph not in out, f"glyph {glyph!r} of {owner} is not unique"
        out[glyph] = term

    for t in TERRAIN.values():
        add(t.glyph, t.term, t.key)
    for o in OBJECTS.values():
        add(o.glyph, o.term, o.key)
        if o.spent_glyph:
            add(o.spent_glyph, o.spent_term, o.key)
    return out


GLYPHS = _glyphs()                     # glyph -> (terminal character, ANSI colour)


def building_glyphs() -> dict[str, str]:
    """Building key -> the tile characters that show it (ripe and spent)."""
    return {o.key: o.glyph + o.spent_glyph for o in BUILDINGS}


def client_defs() -> dict:
    """What the web front end needs to know about the content."""
    return {
        "terrain": {t.key: {"glyph": t.glyph, "label": t.label, "passable": t.passable} for t in TERRAIN.values()},
        "objects": {o.key: {"glyph": o.glyph, "spent_glyph": o.spent_glyph, "label": o.label, "place": o.place,
                            "cost": o.cost, "emoji": o.emoji, "stat": o.stat, "why": o.why,
                            "buildable": bool(o.cost)} for o in OBJECTS.values()},
        "items": {i.key: {"label": i.label, "emoji": i.emoji} for i in ITEMS.values()},
        "actions": {a.kind: {"ticks": a.ticks} for a in ACTIONS.values()},
        "interactions": {i.key: {"meaning": i.meaning} for i in INTERACTIONS.values()},
        "events": dict(EVENT_EMOJI),
        "ages": {"mature": MATURE_AGE, "elder": ELDER_AGE},
    }
