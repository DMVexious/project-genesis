"""World generation. Each preset in ``MAPS`` fills a fresh ``World`` with
terrain and objects; ``settle`` then places the first NPCs."""
from __future__ import annotations

from .defs import START_AGE


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


MAPS = {"barren": barren}
DEFAULT_MAP = "barren"


def generate(world, name: str = DEFAULT_MAP):
    MAPS.get(name, MAPS[DEFAULT_MAP])(world)


def settle(world, count: int):
    """Drop the founders on free, open ground."""
    rng = world.rng
    for _ in range(count):
        for _try in range(10000):
            x, y = rng.below(world.w), rng.below(world.h)
            if world.free(x, y):
                break
        else:
            return  # nowhere left to stand
        hunger = 60 + rng.below(41)
        energy = 60 + rng.below(41)
        social = rng.below(50)
        world.spawn(x, y, 100, hunger, energy, social, START_AGE)
