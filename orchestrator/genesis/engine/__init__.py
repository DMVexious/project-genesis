"""The Genesis game engine: the rules of the world.

Brains (LLM or heuristic) never touch this code. Each tick the engine lists
the actions every NPC may take; a brain answers with an index into that list,
and the engine carries it out.

    engine = Engine()
    state = engine.init(seed, width, height, population)
    state = engine.step({npc_id: (action_index, option_index), ...})
    state = engine.apply([{"op": "give", "npc": 1, "item": "food", "n": 3}])
"""
from __future__ import annotations

from . import actions as _actions  # noqa: F401  (registers the handlers and checks they are complete)
from .mapgen import DEFAULT_MAP, GAME_MAP, MAPS
from .world import Rng, World

__all__ = ["Engine", "World", "Rng", "MAPS", "DEFAULT_MAP", "GAME_MAP"]


class Engine:
    name = "python"

    def __init__(self):
        self.world: World | None = None

    def init(self, seed: int, width: int, height: int, population: int, decay_every: int = 1,
             map_name: str = DEFAULT_MAP) -> dict:
        self.world = World(seed, width, height, population, decay_every, map_name)
        return self.world.state()

    def state(self) -> dict:
        return self.world.state()

    def step(self, choices: dict[int, tuple[int, int]]) -> dict:
        self.world.step(choices)
        return self.world.state()

    def apply(self, commands: list[dict]) -> dict:
        """Change the world from outside the rules; see ``World.apply``."""
        for cmd in commands:
            self.world.apply(cmd)
        return self.world.state()

    def close(self):
        pass
