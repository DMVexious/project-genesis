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


_VILLAGE_START = ["Ash", "Bram", "Cold", "Elder", "Fen", "Gold", "Hollow", "Iron", "Long", "Moss", "North",
                  "Oak", "Red", "Stone", "Thorn", "Wild", "Willow", "Black", "Green", "Lark"]
_VILLAGE_END = ["ford", "wick", "stead", "holm", "dale", "brook", "field", "hearth", "mere", "ridge", "wood",
                "haven", "moor", "well", "barrow"]


def make_village_name(rng: random.Random, taken: set[str]) -> str:
    for _ in range(200):
        name = rng.choice(_VILLAGE_START) + rng.choice(_VILLAGE_END)
        if name not in taken:
            return name
    return f"Village {len(taken) + 1}"


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


# --- Persona ------------------------------------------------------------------
# Every NPC carries one strange belief and one way of talking. Both go into the
# dialogue prompt so each villager sounds like themselves. Beliefs are inherited
# (families share their madness) and spread through conversation, so cults and
# feuding folklores can form; they colour a villager's talk now and then rather
# than being all they talk about. Each belief has a sample line the heuristic
# brain occasionally says.
BELIEFS: list[tuple[str, str]] = [
    ("believes the dead come back as crows and keep score", "That crow owes me three berries. It knows."),
    ("is sure every tree remembers each axe that touched it", "Chop quietly. The old ones are counting."),
    ("thinks the moon is a hole someone forgot to fill in", "Don't look up tonight. It's open again."),
    ("keeps a rock named Grumm and asks it for advice", "Grumm says you smell like a liar."),
    ("believes their shadow is a separate person who hates them", "My shadow tried to trip me again."),
    ("is convinced they already died once and nobody noticed", "I died last spring. Still hungry though."),
    ("worships the first campfire as a god", "The Fire is hungry. Bring it your sticks."),
    ("thinks hunger is a small animal living in the belly", "My belly beast bit me all night."),
    ("believes berries are the eyes of something buried", "I ate an eye today. It blinked first."),
    ("thinks names are borrowed and must be returned at death", "When you die, I want your name."),
    ("insists the sky is sinking a little lower every night", "The sky touched my hair today. Run."),
    ("believes huts steal your dreams while you sleep", "Huts eat dreams. I sleep in dirt."),
    ("thinks the edge of the world is just past the trees", "Past those trees, nothing. I checked."),
    ("is secretly certain one villager is a ghost", "One of us has no footprints. Watch."),
    ("eats dirt for luck and recommends it to everyone", "Eat dirt. Trust me. Tastes like luck."),
    ("believes babies are born knowing a secret, then forget it", "Your baby knew. Now it forgot. Sad."),
    ("plans to marry the wind", "The wind and I are engaged. Jealous?"),
    ("thinks fire is alive and only pretends to be hungry", "Fire's faking. It's full. Always full."),
    ("believes they are the lost king of a forgotten kingdom", "Kneel, peasant. I was king once."),
    ("thinks counting to a hundred makes time stop", "Shh, I'm at sixty. Don't move."),
    ("keeps a tally of every insult ever said to them", "That's forty-one. I wrote it in my head."),
    ("believes stars are other villages' campfires, far away", "Someone up there is cooking. Smell it?"),
    ("is building a tower to talk to the clouds", "Clouds want wood. Lots. Give me yours."),
    ("believes spears have souls and get lonely", "My spear misses blood. Hold still."),
    ("thinks the ground is the back of a sleeping giant", "Step softly. He twitched this morning."),
    ("collects teeth and thinks they whisper prophecies", "My teeth say you die on a Tuesday."),
]

# How someone talks. These are everyday manners of speaking, not theatre: the
# point is that two villagers saying the same thing sound like different people.
VOICES = [
    "keep it short and blunt, few words",
    "grumble about everything, half joking",
    "tease people and joke around",
    "are dry and sarcastic",
    "ramble and lose your thread halfway",
    "talk about yourself by name instead of saying I",
    "are always trying to talk people into a deal",
    "love gossip about the other villagers",
    "fuss over people and ask if they have eaten",
    "overshare about your aches and your stomach",
    "get excited about small things",
    "mutter, sure somebody is up to something",
    "flirt with everyone, badly",
    "sound worn out, like you have seen it all",
    "insult people you like, as a joke",
    "ask nosy questions",
]

PERSONA_MUTATION = 0.25  # chance a child invents a belief/voice instead of inheriting one


def random_persona(rng: random.Random) -> dict[str, int]:
    """Indices into BELIEFS / VOICES (ints keep the frame small)."""
    return {"belief": rng.randrange(len(BELIEFS)), "voice": rng.randrange(len(VOICES))}


def splice_persona(a: dict, b: dict, rng: random.Random) -> dict[str, int]:
    fresh = random_persona(rng)
    return {k: fresh[k] if rng.random() < PERSONA_MUTATION else (a if rng.random() < 0.5 else b)[k]
            for k in ("belief", "voice")}


def belief_text(p: dict) -> str:
    return BELIEFS[p["belief"]][0]


def voice_text(p: dict) -> str:
    return VOICES[p["voice"]]
