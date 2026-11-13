"""Path-finding over walkable tiles. Neighbour order is fixed (N, E, S, W) so
results are reproducible."""
from __future__ import annotations

from .defs import BUILD_SEARCH, DIRS


def scan(world, sx: int, sy: int, glyphs, targets, limit: int) -> dict:
    """One breadth-first flood from (sx, sy) answering several questions at once.

    For every glyph in ``glyphs``: the nearest reachable tile that has such a
    tile next to it. For every NPC in ``targets``: the nearest reachable tile
    within arm's reach of it. Returns ``{glyph or npc id: (dist, dx, dy)}`` where
    (dx, dy) is the first step of the path; anything out of reach is missing.
    """
    w, h = world.w, world.h
    tiles, walk = world.glyphs, world.walk
    want = {g for g in glyphs if world.count[g] > 0}
    near = {t.id: (t.x, t.y) for t in targets}
    found: dict = {}

    def look(x, y, d, fdx, fdy):
        if want:
            i = y * w + x
            for c in (tiles[i - w] if y > 0 else "", tiles[i + 1] if x < w - 1 else "",
                      tiles[i + w] if y < h - 1 else "", tiles[i - 1] if x > 0 else ""):
                if c in want:
                    want.discard(c)
                    found[c] = (d, fdx, fdy)
        if near:
            for tid, (ox, oy) in list(near.items()):
                if abs(x - ox) <= 1 and abs(y - oy) <= 1:
                    del near[tid]
                    found[tid] = (d, fdx, fdy)

    look(sx, sy, 0, 0, 0)
    seen = bytearray(w * h)
    seen[sy * w + sx] = 1
    frontier = [(sx, sy, 0, 0)]
    dist = 0
    while frontier and dist < limit and (want or near):
        dist += 1
        nxt = []
        for x, y, fdx, fdy in frontier:
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                i = ny * w + nx
                if seen[i] or not walk[i]:
                    continue
                seen[i] = 1
                if dist == 1:
                    fdx, fdy = dx, dy
                look(nx, ny, dist, fdx, fdy)
                nxt.append((nx, ny, fdx, fdy))
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
