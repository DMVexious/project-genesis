"""Engine-side NPC: body, needs and what it carries. Names, genes and
personality live in the orchestrator (``sim.NpcMeta``)."""
from __future__ import annotations

from .defs import ATTR_AVERAGE, ATTRS, ITEMS, SKILLS, carry_cap, role_of, skill_need


class Npc:
    __slots__ = ("id", "x", "y", "health", "hunger", "energy", "social", "inv",
                 "age", "alive", "asleep", "cause", "rest", "expecting", "partner",
                 "attrs", "attr_xp", "skills", "skill_xp", "child_attrs", "steps", "village")

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
        self.expecting = 0  # world-time units until this NPC's child is born (0 = not expecting)
        self.partner = -1   # the other parent of that child
        self.child_attrs: dict[str, int] | None = None  # what that child will be made of
        self.attrs: dict[str, int] = {a: ATTR_AVERAGE for a in ATTRS}  # strength, agility...
        self.attr_xp: dict[str, int] = {}
        self.skills: dict[str, int] = {}     # skill -> level
        self.skill_xp: dict[str, int] = {}   # skill -> practice toward the next level
        self.steps = 0                       # tiles walked (walking is exercise too)
        self.village = -1                    # the village they belong to (-1: none)

    def have(self, item: str) -> int:
        return self.inv.get(item, 0)

    def give(self, item: str, n: int = 1) -> int:
        """Add (or with a negative ``n`` remove) items; returns the new count."""
        cap = self.cap(item)
        total = max(0, self.inv.get(item, 0) + n)
        if cap:
            total = min(cap, total)
        if total:
            self.inv[item] = total
        else:
            self.inv.pop(item, None)
        return total

    def room(self, item: str) -> int:
        """How many more of an item this NPC can carry."""
        cap = self.cap(item)
        return max(0, cap - self.inv.get(item, 0)) if cap else 10**9

    def cap(self, item: str) -> int:
        """The most of an item this NPC can carry (0 = no limit)."""
        return carry_cap({"attrs": self.attrs}, item) if ITEMS[item].cap else 0

    def level(self, skill: str) -> int:
        return self.skills.get(skill, 0)

    def take(self, item: str, n: int) -> int:
        """Pick up as many as fit; returns how many were left behind."""
        fit = min(n, self.room(item))
        if fit > 0:
            self.give(item, fit)
        return n - fit

    def pay(self, cost: dict):
        for item, n in cost.items():
            self.give(item, -n)

    def to_json(self) -> dict:
        return {
            "id": self.id, "x": self.x, "y": self.y,
            "health": self.health, "hunger": self.hunger, "energy": self.energy,
            "social": self.social, "inv": dict(self.inv), "age": self.age,
            "alive": self.alive, "asleep": self.asleep, "cause": self.cause,
            "expecting": self.expecting, "village": self.village,
            "attrs": dict(self.attrs), "skills": dict(self.skills), "role": role_of(self.skills),
            # practice toward the next level of each skill being learned: [have, needed]
            "xp": {k: [v, skill_need(self.skills.get(k, 0))] for k, v in self.skill_xp.items() if k in SKILLS},
            "caps": {k: self.cap(k) for k, d in ITEMS.items() if d.cap},
        }
