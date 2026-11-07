"""Genetic traits and the copopulation (trait splicing) algorithm."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

POOLS: dict[str, list[str]] = {
    "aggression": ["Pacifist", "Neutral", "Hostile", "Bloodthirsty"],
    "work": ["Lazy", "Industrious", "Visionary"],
    "social": ["Loner", "Charismatic", "Paranoid"],
}
SLOTS = tuple(POOLS)
MUTATION_RATE = 0.05

TRAIT_HINTS = {
    "Pacifist": "you avoid violence at almost any cost",
    "Neutral": "you fight only when wronged",
    "Hostile": "you distrust others and are quick to fight",
    "Bloodthirsty": "you enjoy violence and take what you want",
    "Lazy": "you prefer resting to working",
    "Industrious": "you love gathering resources",
    "Visionary": "you dream of building a settlement of huts",
    "Loner": "you avoid company",
    "Charismatic": "you seek out others to talk, trade and mate",
    "Paranoid": "you suspect everyone and hold grudges",
}


@dataclass(frozen=True)
class Genome:
    aggression: str
    work: str
    social: str

    def traits(self) -> list[str]:
        return [getattr(self, s) for s in SLOTS]

    def to_dict(self) -> dict:
        return asdict(self)


def random_genome(rng: random.Random) -> Genome:
    return Genome(**{slot: rng.choice(pool) for slot, pool in POOLS.items()})


def splice(a: Genome, b: Genome, rng: random.Random, mutation_rate: float = MUTATION_RATE) -> tuple[Genome, list[str]]:
    """Child genome: each slot is a 50/50 pick from the parents, with a
    ``mutation_rate`` chance of a completely random trait from the master pool.
    Returns the genome and the list of slots that mutated."""
    genes, mutated = {}, []
    for slot, pool in POOLS.items():
        if rng.random() < mutation_rate:
            genes[slot] = rng.choice(pool)
            mutated.append(slot)
        else:
            genes[slot] = getattr(a if rng.random() < 0.5 else b, slot)
    return Genome(**genes), mutated


_ONSETS = ["B", "D", "K", "M", "N", "R", "S", "T", "V", "Z", "Br", "Th", "Kr", "El", "Or", "As"]
_VOWELS = ["a", "e", "i", "o", "u", "ae", "ia", "y"]
_CODAS = ["", "n", "r", "l", "th", "k", "s", "m", "x"]


def make_name(rng: random.Random, taken: set[str]) -> str:
    for _ in range(200):
        name = rng.choice(_ONSETS) + rng.choice(_VOWELS) + rng.choice(_CODAS)
        if rng.random() < 0.4:
            name += rng.choice(_VOWELS) + rng.choice(_CODAS)
        name = name.capitalize()
        if name not in taken:
            return name
    return f"Npc{len(taken)}"


# --- Appearance ---------------------------------------------------------------
# Looks are inherited like traits (50/50 per slot, same mutation rate) but never
# reach the LLM: they exist so families visibly resemble each other.
LOOK_POOLS: dict[str, list[str]] = {
    "skin": ["porcelain", "fair", "tan", "olive", "brown", "deep"],
    "hair": ["black", "brown", "auburn", "blonde", "copper", "ash"],
    "hairstyle": ["bald", "short", "long", "ponytail", "mohawk", "bun", "braids"],
    "clothes": ["tunic", "robe", "vest", "furs", "overalls"],
    "color": ["red", "blue", "green", "ochre", "purple", "teal", "grey", "cream"],
    "hat": ["none", "none", "none", "straw", "hood", "headband", "feather", "flower"],
}


def random_look(rng: random.Random) -> dict[str, str]:
    return {slot: rng.choice(pool) for slot, pool in LOOK_POOLS.items()}


def splice_look(a: dict, b: dict, rng: random.Random, mutation_rate: float = MUTATION_RATE) -> dict[str, str]:
    return {slot: rng.choice(pool) if rng.random() < mutation_rate else (a if rng.random() < 0.5 else b)[slot]
            for slot, pool in LOOK_POOLS.items()}
