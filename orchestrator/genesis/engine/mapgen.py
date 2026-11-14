"""World generation.

Each preset in ``MAPS`` lays out the ground of a fresh ``World`` (grass,
forest, sand, water, mountains...), ``scatter`` then grows things on it
according to the ground they stand on, and ``settle`` finds the founders a
good place to start.

Everything random comes from the world's own generator, in a fixed order, so
a seed always gives the same map.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .defs import OBJECTS, SETTLE_RADIUS, START_AGE, TERRAIN, WATER, WET_REACH, YEAR
from .path import regions


# --- smooth random fields ------------------------------------------------------
class Noise:
    """A smooth random field over the map, values in [0, 1]: a few octaves of
    value noise. ``scale`` is the size of its largest features, in tiles."""

    def __init__(self, rng, w: int, h: int, scale: float, octaves: int = 3):
        self.layers = []
        amp = 1.0
        self.total = 0.0
        for _ in range(octaves):
            s = max(2.0, scale)
            gw, gh = int(w / s) + 3, int(h / s) + 3
            grid = [[rng.below(4096) / 4095 for _ in range(gw)] for _ in range(gh)]
            self.layers.append((s, grid, amp))
            self.total += amp
            amp *= 0.5
            scale /= 2

    def at(self, x: float, y: float) -> float:
        v = 0.0
        for s, grid, amp in self.layers:
            fx, fy = x / s, y / s
            x0, y0 = int(fx), int(fy)
            tx, ty = fx - x0, fy - y0
            tx = tx * tx * (3 - 2 * tx)
            ty = ty * ty * (3 - 2 * ty)
            r0, r1 = grid[y0], grid[y0 + 1]
            a = r0[x0] + (r0[x0 + 1] - r0[x0]) * tx
            b = r1[x0] + (r1[x0 + 1] - r1[x0]) * tx
            v += (a + (b - a) * ty) * amp
        return v / self.total

    def field(self, w: int, h: int) -> list[float]:
        at = self.at
        return [at(x, y) for y in range(h) for x in range(w)]


def levels(values, *fractions) -> list[float]:
    """The values below which the given fractions of ``values`` lie. Cutting a
    field at these makes each kind of ground cover a known share of the map,
    whatever the field's distribution."""
    s = sorted(values)
    n = len(s) - 1
    return [s[min(n, max(0, int(f * n)))] for f in fractions]


# --- water -----------------------------------------------------------------------
def carve(world, x0: int, y0: int, x1: int, y1: int, radius: int = 1, deep: bool = True,
          sway: float = 0.0, wavelength: float = 30.0, ford: tuple[int, int] = (15, 4)):
    """A river from (x0, y0) to (x1, y1): shallow water ``radius`` tiles either
    side of a winding centre line. With ``deep``, the middle is too deep to
    wade except at fords (``ford`` = one every so many tiles, so many long)."""
    rng, w, h, terrain = world.rng, world.w, world.h, world.terrain
    steps = max(abs(x1 - x0), abs(y1 - y0))
    if steps == 0:
        return
    along_x = abs(x1 - x0) >= abs(y1 - y0)
    phase = rng.below(628) / 100
    ford_at = rng.below(ford[0])
    reach = [(dx, dy) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)
             if dx * dx + dy * dy <= radius * radius + 1]
    prev = None
    for s in range(steps + 1):
        t = s / steps
        off = sway * math.sin(s * 2 * math.pi / wavelength + phase) * min(1.0, 6 * t * (1 - t))
        cx = round(x0 + (x1 - x0) * t + (0 if along_x else off))
        cy = round(y0 + (y1 - y0) * t + (off if along_x else 0))
        # (the line may move sideways as it advances: fill in the corner and anything
        # between, so that even the narrowest stream is unbroken)
        cells = [(cx, cy)]
        if prev is not None:
            px, py = prev
            if along_x:
                cells += [(cx, y) for y in range(min(py, cy), max(py, cy) + 1) if y != cy]
            else:
                cells += [(x, cy) for x in range(min(px, cx), max(px, cx) + 1) if x != cx]
        wadeable = (s + ford_at) % ford[0] < ford[1]
        for x, y in cells:
            for dx, dy in reach:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and terrain[yy * w + xx] != "deep":
                    terrain[yy * w + xx] = "shallow"
            if deep and radius >= 1 and not wadeable and 0 <= x < w and 0 <= y < h:
                terrain[y * w + x] = "deep"
        prev = (cx, cy)


def wet_tiles(world) -> bytearray:
    """Which tiles lie within ``WET_REACH`` of water (things grow better there)."""
    w, h = world.w, world.h
    wet = bytearray(w * h)
    for i, t in enumerate(world.terrain):
        if t in WATER:
            x, y = i % w, i // w
            for yy in range(max(0, y - WET_REACH), min(h, y + WET_REACH + 1)):
                row = yy * w
                for xx in range(max(0, x - WET_REACH), min(w, x + WET_REACH + 1)):
                    wet[row + xx] = 1
    return wet


# --- what grows where ------------------------------------------------------------
# terrain -> (object, chance per tile, how strongly it gathers in groves: 0 = evenly spread)
FLORA: dict[str, tuple[tuple[str, float, float], ...]] = {
    "grass": (("tree", 0.035, 1.0), ("bush", 0.028, 1.0), ("fruit", 0.006, 0.0), ("rock", 0.010, 0.0)),
    "forest": (("tree", 0.20, 0.5), ("mushroom", 0.03, 0.0), ("bush", 0.022, 1.0), ("rock", 0.006, 0.0)),
    "dirt": (("tree", 0.03, 1.0), ("bush", 0.016, 1.0), ("rock", 0.04, 0.0)),
    "sand": (("cactus", 0.02, 0.0), ("rock", 0.014, 0.0), ("tree", 0.003, 0.0)),
    "stone": (("rock", 0.09, 0.0), ("tree", 0.05, 1.0), ("bush", 0.012, 0.0)),
    "snow": (("tree", 0.04, 1.0), ("rock", 0.05, 0.0)),
}
WOODY = frozenset({"tree", "fruit"})
# near water: (terrain, object) -> how many times likelier (palms ring the oases; cacti keep to the dry)
WET_FLORA = {("sand", "tree"): 14.0, ("sand", "cactus"): 0.0, ("grass", "bush"): 1.5, ("dirt", "tree"): 1.6,
             ("dirt", "bush"): 1.6}


def scatter(world, boost: dict[str, float] | None = None):
    """Grow trees, bushes, rocks and the rest on the finished ground."""
    rng, w, h = world.rng, world.w, world.h
    boost = boost or {}
    groves = Noise(rng, w, h, 8, 2)    # where trees gather
    patches = Noise(rng, w, h, 6, 2)   # where bushes gather
    wet = wet_tiles(world)
    terrain = world.terrain
    for y in range(h):
        for x in range(w):
            i = y * w + x
            r = rng.below(100000) / 100000  # (one roll per tile, whatever the ground)
            table = FLORA.get(terrain[i])
            if not table:
                continue
            acc = 0.0
            for kind, chance, clump in table:
                chance *= boost.get(kind, 1.0)
                if clump:
                    chance *= max(0.0, 1 + clump * 2.6 * ((groves if kind in WOODY else patches).at(x, y) - 0.5))
                if wet[i]:
                    chance *= WET_FLORA.get((terrain[i], kind), 1.0)
                acc += chance
                if r < acc:
                    world.place(kind, x, y)
                    break


# --- the maps --------------------------------------------------------------------
def barren(world):
    """The original map: scattered rocks, trees and bushes on bare earth."""
    rng = world.rng
    for y in range(world.h):
        for x in range(world.w):
            r = rng.below(100)
            if r < 7:
                world.place("rock", x, y)
            elif r < 13:
                world.place("tree", x, y)
            elif r < 16:
                world.place("bush", x, y)


def _greenery(moist: list[float], forest: float, dirt: float) -> Callable[[int], str]:
    """Lowland ground by how damp it is: the dampest ``forest`` share is forest
    floor, the driest ``dirt`` share bare earth, grass in between."""
    lo, hi = levels(moist, dirt, 1 - forest)
    return lambda i: "forest" if moist[i] > hi else "dirt" if moist[i] < lo else "grass"


def _near_middle(rng, h: int) -> int:
    """A row somewhere around the middle of the map."""
    return h // 2 + rng.below(max(1, h // 8)) - h // 16


def valley(world):
    """A green valley between two mountain ranges, with a river down the middle."""
    w, h, rng = world.w, world.h, world.rng
    rough = Noise(rng, w, h, 16, 4)
    moist = Noise(rng, w, h, 12, 3).field(w, h)
    pools = Noise(rng, w, h, 18, 2).field(w, h)
    height = []
    for y in range(h):
        ridge = abs(2 * y / max(1, h - 1) - 1)  # 0 along the middle, 1 at the top and bottom edges
        for x in range(w):
            height.append(0.5 * rough.at(x, y) + 0.8 * ridge ** 2.6)
    rocky, peak = levels(height, 0.74, 0.86)
    deep, shallow, shore = levels(pools, 0.012, 0.03, 0.045)
    green = _greenery(moist, 0.30, 0.12)
    for i in range(w * h):
        e = height[i]
        if e > peak:
            t = "peak"
        elif e > rocky:
            t = "snow" if e > (rocky + peak) / 2 and moist[i] > 0.5 else "stone"
        elif pools[i] < shore:
            t = "deep" if pools[i] < deep else "shallow" if pools[i] < shallow else "sand"
        else:
            t = green(i)
        world.terrain[i] = t
    carve(world, 0, _near_middle(rng, h), w - 1, _near_middle(rng, h), radius=1, sway=h * 0.11,
          wavelength=max(20.0, w / 2.3))
    world.rebuild()
    scatter(world)


def forest(world):
    """Deep woods with clearings, ponds and a stream."""
    w, h, rng = world.w, world.h, world.rng
    moist = Noise(rng, w, h, 11, 3).field(w, h)
    rough = Noise(rng, w, h, 9, 3).field(w, h)
    pools = Noise(rng, w, h, 14, 2).field(w, h)
    clearing, = levels(moist, 0.24)
    bare, crag = levels(rough, 0.05, 0.97)
    deep, shallow = levels(pools, 0.008, 0.028)
    for i in range(w * h):
        if pools[i] < shallow:
            t = "deep" if pools[i] < deep else "shallow"
        elif rough[i] > crag:
            t = "stone"
        elif moist[i] < clearing:
            t = "dirt" if rough[i] < bare else "grass"
        else:
            t = "forest"
        world.terrain[i] = t
    x0 = w // 4 + rng.below(max(1, w // 2))
    x1 = min(w - 2, max(1, w - 1 - x0 + rng.below(9) - 4))
    carve(world, x0, 0, x1, h - 1, radius=0, deep=False, sway=w * 0.08, wavelength=max(16.0, h / 1.8))
    world.rebuild()
    scatter(world, {"rock": 0.7})


def islands(world):
    """Islands in a warm sea, joined here and there by sandbars."""
    w, h, rng = world.w, world.h, world.rng
    rough = Noise(rng, w, h, 14, 4)
    moist = Noise(rng, w, h, 10, 3).field(w, h)
    cx, cy = w * (0.4 + rng.below(21) / 100), h * (0.4 + rng.below(21) / 100)  # the home island
    size, margin = 0.34 * min(w, h), max(1.0, 0.2 * min(w, h))
    height = []
    for y in range(h):
        for x in range(w):
            hill = max(0.0, 1 - math.hypot(x - cx, y - cy) / size)
            edge = min(1.0, min(x, y, w - 1 - x, h - 1 - y) / margin)
            height.append(0.8 * rough.at(x, y) + 0.4 * hill * hill - 0.5 * (1 - edge) ** 2)
    sea, surf, beach, crag = levels(height, 0.50, 0.60, 0.66, 0.975)
    woods, = levels([moist[i] for i in range(w * h) if height[i] > beach] or [0.5], 0.62)
    for i in range(w * h):
        e = height[i]
        if e <= beach:
            t = "deep" if e <= sea else "shallow" if e <= surf else "sand"
        elif e > crag:
            t = "stone"
        else:
            t = "forest" if moist[i] > woods else "grass"
        world.terrain[i] = t
    world.rebuild()
    scatter(world, {"bush": 1.3, "fruit": 1.5})


def highlands(world):
    """High, hard country: peaks, snowfields, bare rock, and green glens between."""
    w, h, rng = world.w, world.h, world.rng
    height = Noise(rng, w, h, 13, 4).field(w, h)
    moist = Noise(rng, w, h, 10, 3).field(w, h)
    tarn, shore, glen, snowline, peak = levels(height, 0.008, 0.025, 0.40, 0.80, 0.87)
    green = _greenery(moist, 0.30, 0.10)
    for i in range(w * h):
        e = height[i]
        if e > peak:
            t = "peak"
        elif e > snowline:
            t = "snow"
        elif e > glen:
            t = "stone"
        elif e < shore:
            t = "deep" if e < tarn else "shallow"
        else:
            t = green(i)
        world.terrain[i] = t
    world.rebuild()
    scatter(world, {"bush": 1.2})


def desert(world):
    """Sand as far as the eye can see, and a few green oases to live by."""
    w, h, rng = world.w, world.h, world.rng
    rough = Noise(rng, w, h, 12, 3).field(w, h)
    wobble = Noise(rng, w, h, 5, 2)
    flats, outcrop = levels(rough, 0.10, 0.90)
    for i in range(w * h):
        world.terrain[i] = "stone" if rough[i] > outcrop else "dirt" if rough[i] < flats else "sand"
    # oases, a good walk apart: a pool with a sandy shore where palms grow, ringed by grass
    wanted = max(2, round(w * h / 1500))
    spots: list[tuple[int, int, float]] = []
    for _ in range(wanted * 40):
        if len(spots) >= wanted:
            break
        x, y = 7 + rng.below(max(1, w - 14)), 7 + rng.below(max(1, h - 14))
        if all(math.hypot(x - ox, y - oy) > 18 for ox, oy, _ in spots):
            spots.append((x, y, 6.0 + rng.below(25) / 10))
    for ox, oy, size in spots:
        reach = int(size) + 3
        for y in range(max(0, oy - reach), min(h, oy + reach + 1)):
            for x in range(max(0, ox - reach), min(w, ox + reach + 1)):
                d = math.hypot(x - ox, y - oy) + (wobble.at(x, y) - 0.5) * 3
                if d < 1.5:
                    t = "deep"
                elif d < 2.7:
                    t = "shallow"
                elif d < 3.7:
                    t = "sand"
                elif d < size * 0.85:
                    t = "grass"
                elif d < size:
                    t = "dirt"
                else:
                    continue
                world.terrain[y * w + x] = t
    world.rebuild()
    scatter(world, {"bush": 1.8, "fruit": 2.0, "tree": 2.5})  # (what little green there is, is lush)


def delta(world):
    """Rich, wet lowland where a river fans out into the sea."""
    w, h, rng = world.w, world.h, world.rng
    moist = Noise(rng, w, h, 11, 3).field(w, h)
    coastline = Noise(rng, 4, h, 9, 2)
    coast = [round(w * 0.80 + (coastline.at(1, y) - 0.5) * w * 0.12) for y in range(h)]
    green = _greenery(moist, 0.26, 0.05)
    for y in range(h):
        for x in range(w):
            d = x - coast[y]
            world.terrain[y * w + x] = ("deep" if d > 2 else "shallow" if d >= 0 else "sand" if d >= -2
                                        else green(y * w + x))
    # one river in from the west, splitting into three mouths
    fork_x, fork_y = round(w * 0.3), _near_middle(rng, h)
    carve(world, 0, _near_middle(rng, h), fork_x, fork_y, radius=1, sway=h * 0.06, wavelength=max(18.0, w / 3))
    for share in (0.16, 0.5, 0.84):
        y1 = min(h - 1, max(0, round(h * share) + rng.below(5) - 2))
        carve(world, fork_x, fork_y, min(w - 1, coast[y1] + 1), y1, radius=1 if share == 0.5 else 0,
              deep=share == 0.5, sway=h * 0.05, wavelength=max(16.0, w / 4))
    world.rebuild()
    scatter(world, {"bush": 1.2, "fruit": 1.4, "rock": 0.5})


@dataclass(frozen=True)
class MapDef:
    key: str
    label: str
    blurb: str
    build: Callable


MAPS: dict[str, MapDef] = {m.key: m for m in (
    MapDef("valley", "Green Valley", "A river valley between two mountain ranges. A gentle place to begin.", valley),
    MapDef("forest", "Great Forest", "Deep woods with clearings, ponds and a stream. Wood everywhere, open ground scarce.",
           forest),
    MapDef("islands", "Archipelago", "Islands in a warm sea, joined here and there by sandbars.", islands),
    MapDef("highlands", "Highlands", "Peaks, snowfields and bare rock, with green glens between. Hard country.",
           highlands),
    MapDef("desert", "Desert Oasis", "Sand as far as the eye can see, and a few green oases to live by.", desert),
    MapDef("delta", "River Delta", "Rich, wet lowland where a river fans out into the sea. Everything grows.", delta),
    MapDef("barren", "Barren Plain", "Bare earth with scattered trees, rocks and bushes: the original map.", barren),
)}
DEFAULT_MAP = "barren"   # what a bare World() is made of
GAME_MAP = "valley"      # what a new game starts on


def generate(world, name: str = DEFAULT_MAP):
    MAPS.get(name, MAPS[DEFAULT_MAP]).build(world)


# --- the founders ----------------------------------------------------------------
FOOD_WORTH = {o.glyph: o.food for o in OBJECTS.values() if o.food and not o.cost}   # wild food, by its tile


def campsite(world, count: int, label: list[int], main: int) -> tuple[int, int]:
    """A good place to start, on the stretch of land labelled ``main``: food and
    wood within a short walk, water not far, and room to build. (One of the
    best few spots, so the same map is not always settled alike.)"""
    w, h, rng = world.w, world.h, world.rng
    rows = world.rows()
    wood = OBJECTS["tree"].glyph
    water = TERRAIN["shallow"].glyph
    open_ground = "".join(t.glyph for t in TERRAIN.values() if t.passable and t.buildable)
    margin = min(6, w // 5, h // 5)
    step = 3 if w * h <= 6000 else 4
    scored = []
    for cy in range(margin, h - margin, step):
        for cx in range(margin, w - margin, step):
            if label[cy * w + cx] != main or not world.free(cx, cy):
                continue
            food = trees = wet = room = 0
            for y in range(max(0, cy - 8), min(h, cy + 9)):
                near = rows[y][max(0, cx - 8):cx + 9]
                trees += near.count(wood)
                for glyph, worth in FOOD_WORTH.items():
                    food += worth * near.count(glyph)
                if abs(y - cy) <= 6:
                    wet += rows[y][max(0, cx - 6):cx + 7].count(water)
                if abs(y - cy) <= 3:
                    close = rows[y][max(0, cx - 3):cx + 4]
                    room += sum(close.count(g) for g in open_ground)
            score = 3 * min(food, 30) + 2 * min(trees, 14) + min(wet, 8)
            if room < 14 + count:
                score -= 1000  # hemmed in
            score -= 40 * max(0, 6 - food)   # too little to eat...
            score -= 40 * max(0, 4 - trees)  # ...or to build with, and it is no place to settle
            scored.append((score, cx, cy))
    if not scored:
        return next(((i % w, i // w) for i in range(w * h) if label[i] == main), (w // 2, h // 2))
    scored.sort(key=lambda s: (-s[0], s[2], s[1]))
    _, cx, cy = scored[rng.below(min(4, len(scored)))]
    return cx, cy


def settle(world, count: int):
    """Drop the founders together on free ground at a good campsite, all on the
    same stretch of land. (Crowds spread out: the starting area grows with the
    number of founders.)"""
    rng, w = world.rng, world.w
    label, sizes = regions(world)
    if not sizes:
        return  # nowhere to stand at all
    main = max(range(len(sizes)), key=lambda k: sizes[k])  # the biggest stretch of connected land
    cx, cy = world.start = campsite(world, count, label, main)
    for i in range(count):
        radius = SETTLE_RADIUS + i // 4
        for _try in range(10000):
            if _try and _try % 40 == 0:
                radius += 1  # crowded or rocky: look a little further out
            x = cx - radius + rng.below(2 * radius + 1)
            y = cy - radius + rng.below(2 * radius + 1)
            if world.free(x, y) and label[y * w + x] == main:
                break
        else:
            return  # nowhere left to stand
        hunger = 60 + rng.below(41)
        energy = 60 + rng.below(41)
        social = rng.below(50)
        age = START_AGE + (rng.below(9) - 2) * YEAR  # the founders are 18 to 26
        world.spawn(x, y, 100, hunger, energy, social, age).attrs = world.roll_attrs()
