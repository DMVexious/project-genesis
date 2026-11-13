"""Engine-side NPC: body, needs and what it carries. Names, genes and
personality live in the orchestrator (``sim.NpcMeta``)."""
from __future__ import annotations

from .defs import ITEMS


class Npc:
    __slots__ = ("id", "x", "y", "health", "hunger", "energy", "social", "inv",
                 "age", "alive", "asleep", "cause", "rest")

    def __init__(self, id_, x, y, health, hunger, energy, social, age):
        self.id, self.x, self.y = id_, x, y
        # "hunger" is fullness: 100 = fed, 0 = starving
        self.health, self.hunger, self.energy, self.social = health, hunger, energy, social
        self.inv: dict[str, int] = {}
        self.age = age
        self.alive = True
        self.asleep = False
        self.cause = ""
        self.rest = 0  # mating cooldown, counts down in world time

    def have(self, item: str) -> int:
        return self.inv.get(item, 0)

    def give(self, item: str, n: int = 1) -> int:
        """Add (or with a negative ``n`` remove) items; returns the new count."""
        cap = ITEMS[item].cap
        total = max(0, self.inv.get(item, 0) + n)
        if cap:
            total = min(cap, total)
        if total:
            self.inv[item] = total
        else:
            self.inv.pop(item, None)
        return total

    def pay(self, cost: dict):
        for item, n in cost.items():
            self.give(item, -n)

    def to_json(self) -> dict:
        return {
            "id": self.id, "x": self.x, "y": self.y,
            "health": self.health, "hunger": self.hunger, "energy": self.energy,
            "social": self.social, "inv": dict(self.inv), "age": self.age,
            "alive": self.alive, "asleep": self.asleep, "cause": self.cause,
        }
