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

import math
from dataclasses import dataclass, field
from typing import Callable

# --- Tuning ------------------------------------------------------------------
STAT_MAX = 100
EAT_HUNGER = 30
SLEEP_ENERGY = 20
COST_ATTACK = 6
COST_DEFEND = 4
COST_MATE = 15
YEAR = 20                # world-time units in a year of someone's life (~22 s at the default speed)
MATURE_AGE = 16 * YEAR   # children grow up
START_AGE = 20 * YEAR    # the founders (give or take a few years)
POP_CAP = 30
SIGHT = 20
GOTO_LIMIT = 40          # how far (in steps) someone knows their surroundings: nearer things can be walked to
BUILD_SEARCH = 6
MAX_TARGETS = 3
TALK_SOCIAL = 5
MATE_SOCIAL = 30
MATE_COOLDOWN = 100      # world-time units a parent rests before another child
MATE_PRIVACY = 1         # nobody else may be this close when two NPCs mate
GESTATION = 40           # world-time units between conceiving and the birth
SETTLE_RADIUS = 3        # the founders start within this many tiles of each other
DROP_TTL = 300           # world-time units before things left on the ground rot away
ELDER_AGE = 60 * YEAR    # from here on, each unit of world time may be the last
OLD_AGE_ODDS = 65000     # death chance per unit = (age - ELDER_AGE) / OLD_AGE_ODDS (most die at 70-80)

DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))  # N, E, S, W
STEP_NAMES = {(0, -1): "north", (1, 0): "east", (0, 1): "south", (-1, 0): "west"}


def years(age: int) -> int:
    """Age in years, from the engine's age in units of world time."""
    return age // YEAR


# --- Attributes and skills -----------------------------------------------------
# Attributes are what someone is made of: partly inherited, slowly built up by
# what they do. Skills are what they have learned: each has levels, earned with
# practice. Both change what a villager is good at for the rest of their life.
ATTR_MIN, ATTR_MAX, ATTR_AVERAGE = 1, 10, 5
SKILL_MAX = 10


@dataclass(frozen=True)
class AttrDef:
    key: str
    label: str
    emoji: str
    blurb: str


ATTRS: dict[str, AttrDef] = {a.key: a for a in (
    AttrDef("strength", "Strength", "💪", "chops faster, hits harder, carries more"),
    AttrDef("agility", "Agility", "🦶", "walks and gathers faster, harder to hit"),
    AttrDef("intelligence", "Intelligence", "🧠", "learns faster, builds and crafts faster"),
    AttrDef("endurance", "Endurance", "🫀", "tires more slowly"),
    AttrDef("charisma", "Charisma", "✨", "persuades, charms and converts"),
)}


@dataclass(frozen=True)
class SkillDef:
    key: str
    label: str
    role: str                  # what someone good at it is called
    emoji: str
    attrs: tuple[str, ...]     # practising it also builds these (the first one most)
    blurb: str
    renown: float = 1.0        # how much it takes to be known for it (everyone forages and talks)


SKILLS: dict[str, SkillDef] = {s.key: s for s in (
    SkillDef("woodcutting", "Woodcutting", "Woodcutter", "🪓", ("strength", "endurance"),
             "fells trees faster and gets more wood from each"),
    SkillDef("foraging", "Foraging", "Forager", "🍒", ("agility", "endurance"),
             "gathers faster and finds more food", renown=0.75),
    SkillDef("building", "Building", "Builder", "🔨", ("intelligence", "strength"), "builds faster", renown=1.25),
    SkillDef("crafting", "Crafting", "Crafter", "🛠️", ("intelligence", "agility"), "crafts faster", renown=1.25),
    SkillDef("combat", "Combat", "Fighter", "⚔️", ("strength", "agility"), "hits more often and harder",
             renown=1.1),
    SkillDef("speech", "Speech", "Talker", "💬", ("charisma", "intelligence"),
             "better at persuading, trading and courting", renown=0.6),
)}
ROLE_LEVEL = 3                 # how good someone must be at their best skill to be known for it
WALK_EXERCISE = 4              # every so many steps walked count toward agility


def skill_need(level: int) -> int:
    """Practice (xp) needed to go from ``level`` to the next. (Ten trees felled
    make a level-3 woodcutter; a lifetime at it, a master.)"""
    return 20 + 12 * level


def attr_need(value: int) -> int:
    """Exercise (xp) needed to raise an attribute from ``value`` by one."""
    return 60 + 30 * value


def attr_of(stats: dict, key: str) -> int:
    return stats.get("attrs", {}).get(key, ATTR_AVERAGE)


def skill_of(stats: dict, key: str) -> int:
    return stats.get("skills", {}).get(key, 0)


def role_of(skills: dict) -> str:
    """The skill someone is known for: what they are best at, once they are
    good enough at it. Skills everybody picks up (foraging, talking) take more
    to stand out than rarer ones."""
    order = list(SKILLS)
    known = [k for k in skills if k in SKILLS and skills[k] * SKILLS[k].renown >= ROLE_LEVEL]
    return max(known, key=lambda k: (skills[k] * SKILLS[k].renown, -order.index(k)), default="")


def learn_rate(stats: dict) -> float:
    """The clever learn faster."""
    return 0.7 + 0.06 * attr_of(stats, "intelligence")


def tire_rate(stats: dict) -> float:
    """How much of an action's energy cost someone really pays (the tough pay less)."""
    return 1.3 - 0.06 * attr_of(stats, "endurance")


def carry_cap(stats: dict, item: str) -> int:
    """How many of an item someone can carry (the strong carry more)."""
    cap = ITEMS[item].cap
    return cap if cap <= 1 else max(1, round(cap * (0.7 + 0.06 * attr_of(stats, "strength"))))


# --- Terrain -----------------------------------------------------------------
@dataclass(frozen=True)
class TerrainDef:
    key: str
    glyph: str                 # one character in the tile view
    label: str
    passable: bool = True
    buildable: bool = True
    fertile: float = 1.0       # how fast things grow on it (1 = bare earth; 0 = nothing grows)
    move_cost: int = 1         # walking onto it takes this many times longer
    term: tuple[str, str] = ("·", "33")  # terminal glyph, ANSI colour
    color: str = "#7a5a3a"     # on the minimap


# (Listed from the lowest-lying to the highest: where two kinds meet, the front
# end draws the later one spilling over the earlier one.)
TERRAIN: dict[str, TerrainDef] = {t.key: t for t in (
    TerrainDef("deep", "≈", "deep water", passable=False, buildable=False, fertile=0.0,
               term=("≈", "34"), color="#2f6f9f"),
    TerrainDef("shallow", "~", "shallow water", buildable=False, fertile=0.0, move_cost=3,
               term=("~", "36"), color="#5fa9c9"),
    TerrainDef("sand", ":", "sand", fertile=0.5, term=("∴", "93"), color="#d9c38a"),
    TerrainDef("dirt", ".", "bare earth", term=("·", "33"), color="#7a5a3a"),
    TerrainDef("stone", "_", "rocky ground", fertile=0.4, term=("∙", "37"), color="#8a8578"),
    TerrainDef("grass", ",", "grass", fertile=1.3, term=("„", "32"), color="#5f8f3c"),
    TerrainDef("forest", ";", "forest floor", fertile=1.15, term=("‥", "32;2"), color="#456a30"),
    TerrainDef("snow", "*", "snow", fertile=0.25, move_cost=2, term=("*", "97"), color="#e8eef2"),
    TerrainDef("peak", "^", "mountain", passable=False, buildable=False, fertile=0.0,
               term=("▲", "37;1"), color="#6f6a63"),
)}
WATER = frozenset({"deep", "shallow"})
WET_REACH = 2            # land this close to water is moist: things grow faster on it
WET_BONUS = 1.25
MIN_FERTILITY = 0.2      # (so that nothing takes forever to grow back)


# --- Items -------------------------------------------------------------------
@dataclass(frozen=True)
class ItemDef:
    key: str
    label: str
    emoji: str
    cap: int = 0               # most one NPC can carry (0 = no limit)
    value: int = 1             # what one is worth in a trade
    gift: int = 1              # how many change hands when it is given away
    damage: int = 0            # added to every hit while carried


ITEMS: dict[str, ItemDef] = {i.key: i for i in (
    ItemDef("wood", "wood", "🪵", cap=24, gift=2),
    ItemDef("food", "food", "🍒", cap=12, value=2),
    ItemDef("spear", "spear", "🗡️", cap=1, value=6, damage=12),
)}


def trade_terms(give: str, get: str) -> tuple[int, int]:
    """A fair swap of two kinds of item: (how many are given, how many are got)."""
    a, b = ITEMS[give].value, ITEMS[get].value
    g = math.gcd(a, b)
    return b // g, a // g


def items_text(items: dict) -> str:
    return " and ".join(f"{n} {ITEMS[k].label}" for k, n in items.items())


@dataclass(frozen=True)
class RecipeDef:
    key: str                   # the item made
    cost: dict = field(default_factory=dict)
    energy: int = 5
    ticks: int = 10
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
    drops: bool = False        # the yield falls on the ground (to be picked up) instead of into the hands
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
              harvest="chop", gives={"wood": 4}, harvest_energy=5, regrow=60, drops=True),
    ObjectDef("bush", "a berry bush", "berry bush", "B", "b",
              term=("✿", "35"), spent_term=("✽", "90"),
              harvest="forage", gives={"food": 2}, harvest_energy=2, regrow=40),
    # (wild food differs by country: orchards on the grass, cacti in the desert, mushrooms under the trees)
    ObjectDef("fruit", "an apple tree", "apple tree", "A", "a",
              term=("♠", "91"), spent_term=("♠", "32"),
              harvest="forage", gives={"food": 3}, harvest_energy=3, regrow=70),
    ObjectDef("cactus", "a cactus", "prickly pear cactus", "K", "k",
              term=("ψ", "92"), spent_term=("ψ", "32"),
              harvest="forage", gives={"food": 1}, harvest_energy=3, regrow=50),
    ObjectDef("mushroom", "a mushroom patch", "mushroom patch", "M", "m",
              term=("♧", "95"), spent_term=("♧", "90"),
              harvest="forage", gives={"food": 1}, harvest_energy=1, regrow=30),
    ObjectDef("hut", "a hut", "hut", "H", term=("⌂", "93"),
              cost={"wood": 10}, build_energy=15, build_ticks=18,
              why="becomes your home; best sleep and healing", emoji="🔨", stat="huts",
              home=True, sleep_energy=35, sleep_heal=2),
    ObjectDef("farm", "a farm", "farm", "F", "f",
              term=("❦", "92"), spent_term=("ᵥ", "32"),
              harvest="forage", gives={"food": 3}, harvest_energy=2, regrow=25,
              cost={"wood": 6}, build_energy=8, build_ticks=12, starts_spent=True,
              why="crops that regrow 3 food again and again", emoji="🌱", stat="farms"),
    ObjectDef("fire", "a campfire", "campfire", "C", term=("♨", "91"),
              cost={"wood": 3}, build_energy=4, build_ticks=7,
              why="cooked meals fill more, warmer sleep, a gathering spot", emoji="🔥", stat="fires",
              sleep_energy=28, cook_bonus=15),
)}

BUILDINGS: tuple[ObjectDef, ...] = tuple(o for o in OBJECTS.values() if o.cost)

# Path-finding looks for the nearest of each kind of place worth walking to (see
# path.scan). Each such glyph has a bit; every tile keeps a mask of the kinds it
# is next to, so a search reads one number per tile instead of four neighbours.
GOTO_BIT: dict[str, int] = {o.glyph: 1 << k for k, o in enumerate(o for o in OBJECTS.values() if o.goto)}
BIT_GLYPH: dict[int, str] = {bit: glyph for glyph, bit in GOTO_BIT.items()}


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
    skill: str = ""                          # the skill it uses and trains
    xp: int = 0                              # practice gained each time it is done
    attr: str = ""                           # the attribute that makes it quicker
    timed: bool = False                      # takes effect when the work is finished, not when it starts
    walk: bool = False                       # a multi-tick "keep walking there" goal


def harvest_yield(spec: ObjectDef, level: int) -> dict:
    """What harvesting something gives someone with that skill level: the
    practised get more out of it (+1 at level 3, 6 and 9)."""
    return {item: amount + level // 3 for item, amount in spec.gives.items()}


def _d_harvest(a, names, me):
    """"Chop down the adjacent tree (it drops 4 wood...)", "Gather food from the adjacent berry bush (+2 food)"."""
    o = OBJECTS[a["what"]]
    gives = items_text(harvest_yield(o, skill_of(me, ACTIONS[a["kind"]].skill)))
    if o.drops:
        return f"Chop down the adjacent {o.place} (it drops {gives}: walk over it to pick it up)"
    return f"Gather from the adjacent {o.place} (+{gives})"


def _d_collect(a, names, me):
    where = "at your feet" if a["dist"] == 0 else f"{a['dist']} steps away"
    owner = a.get("owner", -1)
    whose = "" if owner in (-1, me.get("id")) else f"; it is {names.get(owner, 'someone else')}'s, they will mind"
    return f"Pick up the {items_text(a['items'])} lying on the ground ({where}{whose})"


def soil_text(soil: float) -> str:
    """How good the ground is for growing things, in a word or two."""
    return "rich soil" if soil >= 1.25 else "poor soil" if soil < 0.8 else "fair soil"


def _d_build(a, names, me):
    o = OBJECTS[a["what"]]
    soil = f"; {soil_text(a['soil'])} here" if o.regrow and "soil" in a else ""  # (it matters to what grows)
    return f"Build a {o.place} here (costs {cost_text(o.cost)}; {o.why}{soil})"


def _d_craft(a, names, me):
    r = RECIPES[a["what"]]
    return f"Craft a {ITEMS[r.key].label} (costs {cost_text(r.cost)}, {r.why})"


def _name(names, a):
    return names.get(a["target"], "?")


ACTIONS: dict[str, ActionDef] = {a.kind: a for a in (
    # (order here is the order actions are offered in; "sleep" must stay first)
    ActionDef("sleep", 10, lambda a, names, me: "Sleep (restore energy)"),
    ActionDef("eat", 4, lambda a, names, me: f"Eat food (you carry {me['inv'].get('food', 0)})", timed=True),
    ActionDef("chop", 9, _d_harvest, timed=True, skill="woodcutting", xp=10, attr="strength"),
    ActionDef("forage", 6, _d_harvest, timed=True, skill="foraging", xp=5, attr="agility"),
    ActionDef("build", 12, _d_build, ident=("what",), timed=True, skill="building", xp=14, attr="intelligence"),
    ActionDef("craft", 10, _d_craft, ident=("what",), timed=True, skill="crafting", xp=12, attr="intelligence"),
    ActionDef("interact", 7, lambda a, names, me: f"Interact with {_name(names, a)}", ident=("target",)),
    ActionDef("collect", 2, _d_collect, walk=True, attr="agility"),
    ActionDef("goto", 2, lambda a, names, me:
              f"Walk toward the nearest {OBJECTS[a['what']].place} ({a['dist']} steps)", ident=("what",), walk=True, attr="agility"),
    ActionDef("goto_npc", 2, lambda a, names, me:
              f"Walk toward {_name(names, a)} ({a['dist']} steps)", ident=("target",), walk=True, attr="agility"),
    ActionDef("wander", 3, lambda a, names, me: "Wander randomly", attr="agility"),
    # (only for someone the player controls: one step whichever way the player says)
    ActionDef("step", 2, lambda a, names, me: f"Step {STEP_NAMES[(a['dx'], a['dy'])]}", ident=("dx", "dy"),
              attr="agility"),
)}


def describe_action(act: dict, names: dict[int, str], me: dict) -> str:
    d = ACTIONS.get(act["kind"])
    return d.describe(act, names, me) if d else act["kind"]


def action_ticks(act: dict) -> int:
    """Base duration of an action in ticks. (A step onto slow ground, such as
    wading into water, carries its ``cost``: that many times longer.)"""
    kind = act["kind"]
    if kind == "build":
        return OBJECTS[act["what"]].build_ticks
    if kind == "craft":
        return RECIPES[act["what"]].ticks
    return (ACTIONS[kind].ticks if kind in ACTIONS else 2) * act.get("cost", 1)


def speed_factor(stats: dict, act: dict) -> float:
    """How long an action takes this NPC, as a multiple of its base duration:
    practice (the action's skill) and the right attribute both make it quicker."""
    d = ACTIONS.get(act["kind"])
    if d is None:
        return 1.0
    f = 1.0
    if d.skill:
        f /= 1 + 0.07 * skill_of(stats, d.skill)          # level 10: about 40% quicker
    if d.attr:
        f *= 1.15 - 0.03 * attr_of(stats, d.attr)         # 5 is average; 10: 15% quicker
    return f


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
    ticks: int = 7             # how long both stay busy with it
    skill: str = "speech"      # the skill it trains in whoever starts it
    xp: int = 5
    refused: str = ""          # "they want ..." after the listener refused


INTERACTIONS: dict[str, InteractionDef] = {i.key: i for i in (
    InteractionDef("talk", "just talk", "to talk"),
    InteractionDef("fight", "attack them", "to fight you", ticks=5, skill="combat", xp=10),
    InteractionDef("trade", "offer to swap your food and wood", "to trade",
                   consent=True, xp=8, refused="to trade, and you refused"),
    InteractionDef("give", "give them something of yours for nothing", "to give you something"),
    InteractionDef("mate", "ask them to start a family with you (you are fond of each other; "
                           "a child carries on your line)", "to start a family with you",
                   consent=True, ticks=18, refused="to start a family with you, and you said no"),
)}


def option_text(option: str, mine: bool = True) -> str:
    """What an interaction option means, in words. Options may carry details:
    ``trade:wood:food`` (give wood, get food) and ``give:food``. ``mine`` says
    whose point of view to word it from: the one proposing, or the one asked."""
    base, *args = option.split(":")
    if base == "trade" and len(args) == 2:
        n, m = trade_terms(*args)
        give, get = f"{n} {ITEMS[args[0]].label}", f"{m} {ITEMS[args[1]].label}"
        return f"swap: give them {give}, get {get}" if mine else f"to swap: they give you {give}, you give them {get}"
    if base == "give" and args:
        what = f"{ITEMS[args[0]].gift} {ITEMS[args[0]].label}"
        return f"give them {what} for nothing, to help them out" if mine else f"to give you {what}, for nothing"
    d = INTERACTIONS.get(base)
    return option if d is None else (d.meaning if mine else d.they_want)


# --- Events ------------------------------------------------------------------
# Engine event type -> emoji shown over the NPC. Every type listed is also sent
# to the front end to be animated.
EVENT_EMOJI = {"chop": "🪓", "build": "🔨", "attack": "💢", "birth": "👶", "conceive": "❤️", "forage": "🍒",
               "eat": "🍖", "trade": "🤝", "gift": "🎁", "talk": "💬", "craft": "🗡️", "death": "💀",
               "sleep": "", "drop": "", "pickup": "", "levelup": "⭐", "attrup": "⭐"}
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
        # (in drawing order: where two kinds of ground meet, the later one spills over the earlier)
        "terrain": {t.key: {"glyph": t.glyph, "label": t.label, "passable": t.passable, "layer": i,
                            "color": t.color, "water": t.key in WATER} for i, t in enumerate(TERRAIN.values())},
        "objects": {o.key: {"glyph": o.glyph, "spent_glyph": o.spent_glyph, "label": o.label, "place": o.place,
                            "cost": o.cost, "emoji": o.emoji, "stat": o.stat, "why": o.why,
                            "buildable": bool(o.cost)} for o in OBJECTS.values()},
        "items": {i.key: {"label": i.label, "emoji": i.emoji, "cap": i.cap, "value": i.value}
                  for i in ITEMS.values()},
        "actions": {a.kind: {"ticks": a.ticks, "timed": a.timed} for a in ACTIONS.values()},
        "interactions": {i.key: {"meaning": i.meaning} for i in INTERACTIONS.values()},
        "events": dict(EVENT_EMOJI),
        "ages": {"mature": years(MATURE_AGE), "elder": years(ELDER_AGE), "gestation": GESTATION},
        "attrs": {a.key: {"label": a.label, "emoji": a.emoji, "blurb": a.blurb} for a in ATTRS.values()},
        "skills": {s.key: {"label": s.label, "role": s.role, "emoji": s.emoji, "blurb": s.blurb}
                   for s in SKILLS.values()},
        "limits": {"attr": ATTR_MAX, "skill": SKILL_MAX},
    }
