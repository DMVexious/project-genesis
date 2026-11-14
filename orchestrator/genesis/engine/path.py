"""Path-finding over walkable tiles. Neighbour order is fixed (N, E, S, W) so
results are reproducible."""
from __future__ import annotations

from .defs import BIT_GLYPH, BUILD_SEARCH, DIRS, GOTO_BIT

RARE = 48  # kinds of place with at most this many instances get a cheap "is any even close?" check first


def scan(world, sx: int, sy: int, glyphs, targets, limit: int, cells=()) -> dict:
    """One breadth-first flood from (sx, sy) answering several questions at once.

    For every glyph in ``glyphs``: the nearest reachable tile that has such a
    tile next to it. For every NPC in ``targets``: the nearest reachable tile
    within arm's reach of it. Returns ``{glyph or npc id: (dist, dx, dy)}`` where
    (dx, dy) is the first step of the path; anything further than ``limit``
    steps away (or unreachable) is missing.
    If ``cells`` (tile indices) is given, the nearest of them that can be stood
    on is returned under ``"cell"`` as (dist, dx, dy, x, y).

    The flood stops as soon as everything asked for is found, so it pays to ask
    only for what can be there: a kind with no instance on the map, or (for
    rare kinds) none within ``limit`` tiles as the crow flies, is not searched.
    """
    w = world.w
    size = w * world.h
    walk, near, count, where = world.walk, world.near, world.count, world.where

    want = 0  # bits of the kinds still being looked for
    for g in glyphs:
        n = count[g]
        if n <= 0:
            continue
        if n <= RARE and not any(abs(i % w - sx) + abs(i // w - sy) <= limit + 1 for i in where[g]):
            continue  # every one of them is too far to walk to, however the paths run
        want |= GOTO_BIT[g]

    reach: dict[int, list[int]] = {}  # tile -> the targets it is within arm's reach of
    for t in targets:
        for y in range(max(0, t.y - 1), min(world.h, t.y + 2)):
            for x in range(max(0, t.x - 1), min(w, t.x + 2)):
                reach.setdefault(y * w + x, []).append(t.id)
    todo = len(targets)
    spots = set(cells)
    found: dict = {}

    def record(i: int, d: int, f: int):
        """Note everything that tile ``i`` (``d`` steps away, first step ``f``) answers."""
        nonlocal want, todo, spots
        dx, dy = DIRS[f] if d else (0, 0)
        m = near[i] & want
        if m:
            want &= ~m
            while m:
                bit = m & -m
                found[BIT_GLYPH[bit]] = (d, dx, dy)
                m ^= bit
        if todo and i in reach:
            for tid in reach[i]:
                if tid not in found:
                    found[tid] = (d, dx, dy)
                    todo -= 1
        if spots and i in spots:
            found["cell"] = (d, dx, dy, i % w, i // w)
            spots = set()  # the first one reached is the nearest

    start = sy * w + sx
    record(start, 0, 0)
    seen = bytearray(size)
    seen[start] = 1
    frontier = [start << 2]  # each entry: tile index * 4 + direction of the path's first step
    dist = 0
    while frontier and dist < limit and (want or todo or spots):
        dist += 1
        first = dist == 1
        nxt: list[int] = []
        push = nxt.append
        for code in frontier:
            i = code >> 2
            f = code & 3
            x = i % w
            j = i - w                                   # north
            if j >= 0 and not seen[j] and walk[j]:
                seen[j] = 1
                if first:
                    f = 0
                if near[j] & want or (todo and j in reach) or (spots and j in spots):
                    record(j, dist, f)
                push(j << 2 | f)
            if x != w - 1:                              # east
                j = i + 1
                if not seen[j] and walk[j]:
                    seen[j] = 1
                    if first:
                        f = 1
                    if near[j] & want or (todo and j in reach) or (spots and j in spots):
                        record(j, dist, f)
                    push(j << 2 | f)
            j = i + w                                   # south
            if j < size and not seen[j] and walk[j]:
                seen[j] = 1
                if first:
                    f = 2
                if near[j] & want or (todo and j in reach) or (spots and j in spots):
                    record(j, dist, f)
                push(j << 2 | f)
            if x:                                       # west
                j = i - 1
                if not seen[j] and walk[j]:
                    seen[j] = 1
                    if first:
                        f = 3
                    if near[j] & want or (todo and j in reach) or (spots and j in spots):
                        record(j, dist, f)
                    push(j << 2 | f)
        frontier = nxt
    return found


def build_site(world, n):
    """Nearest free, buildable tile (by walking distance) for something new."""
    w = world.w
    seen = bytearray(w * world.h)
    seen[n.y * w + n.x] = 1
    frontier = [(n.x, n.y)]
    for _ in range(BUILD_SEARCH):
        nxt = []
        for x, y in frontier:
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if not world.passable(nx, ny) or seen[ny * w + nx]:
                    continue
                seen[ny * w + nx] = 1
                if world.free(nx, ny):
                    return nx, ny
                nxt.append((nx, ny))
        frontier = nxt
    return None


def regions(world) -> tuple[list[int], list[int]]:
    """Which connected stretch of walkable ground each tile belongs to.

    Returns (label per tile, size per label); tiles that cannot be walked on
    are labelled -1. Two tiles with the same label can reach each other.
    """
    w = world.w
    size = w * world.h
    walk = world.walk
    label = [-1] * size
    sizes: list[int] = []
    for start in range(size):
        if not walk[start] or label[start] != -1:
            continue
        k = len(sizes)
        label[start] = k
        stack = [start]
        n = 0
        while stack:
            i = stack.pop()
            n += 1
            x = i % w
            for j in (i - w, i + w, i + 1 if x != w - 1 else -1, i - 1 if x else -1):
                if 0 <= j < size and walk[j] and label[j] == -1:
                    label[j] = k
                    stack.append(j)
        sizes.append(n)
    return label, sizes
