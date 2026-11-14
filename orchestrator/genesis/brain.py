"""NPC cognition.

``LlamaBrain`` sends prompts to a llama.cpp server (``llama-server``) through
its OpenAI-compatible chat endpoint. Every request carries a GBNF grammar
that only admits valid output: for action choice the grammar is exactly the
set of valid indices, and for interactions it is ``<option> | <1-10 words>``.
The model therefore cannot hallucinate an action the engine did not offer.

``HeuristicBrain`` is a trait-driven utility AI with the same interface. It
is used when no LLM server is reachable and as a per-call fallback if a
request fails, so the simulation never stalls on inference.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field

from .engine.defs import (ACTIONS, ATTR_AVERAGE, ATTRS, BUILDINGS, INTERACTIONS, ITEMS, MATURE_AGE, OBJECTS,
                          SKILLS, attr_of, cost_text, describe_action, option_text, role_of, skill_of,
                          years)  # noqa: F401  (describe_action is re-exported)
from .traits import BELIEFS, TRAIT_HINTS, Genome, belief_text, voice_text

log = logging.getLogger(__name__)

MAX_WORDS = 14


@dataclass
class NpcView:
    """Everything a brain may know about one NPC this tick."""
    id: int
    name: str
    genome: Genome
    stats: dict
    actions: list[dict] = field(default_factory=list)
    huts_nearby: int = 0  # huts within 5 tiles
    nearby: dict = field(default_factory=dict)  # {"hut": n, "farm": n, "fire": n} within 5 tiles
    home: int | None = None  # walking-ish distance to the hut this NPC built, None = homeless
    elder: bool = False
    persona: dict = field(default_factory=dict)  # {"belief": i, "voice": j}, see traits.BELIEFS/VOICES
    ground: str = ""  # what they are standing on: "grass", "shallow water"...


@dataclass
class Interaction:
    me: NpcView
    other: NpcView
    options: list[str]
    memories: list[str]
    grudge: int
    news: list[str] = field(default_factory=list)  # recent village events, newest last


# --- Prompt assembly ---------------------------------------------------------

def have(stats: dict, item: str) -> int:
    """How many of an item an NPC carries (``stats`` is the engine's NPC record)."""
    return stats.get("inv", {}).get(item, 0)


def abilities(stats: dict) -> dict:
    """An NPC's attributes and skills, worded for a prompt."""
    skills = {SKILLS[k].label.lower(): lvl for k, lvl in stats.get("skills", {}).items() if lvl > 0 and k in SKILLS}
    out = {"attributes_1_to_10": {ATTRS[k].label.lower(): v for k, v in stats.get("attrs", {}).items() if k in ATTRS}}
    if skills:
        out["skill_levels"] = skills
    role = role_of(stats.get("skills", {}))
    if role:
        out["known_as"] = f"the village's {SKILLS[role].role.lower()}"
    return out


def role_name(stats: dict) -> str:
    role = role_of(stats.get("skills", {}))
    return SKILLS[role].role.lower() if role else ""


def building_rules() -> str:
    """One sentence listing what can be built, straight from the content registry."""
    return "You decide what to build: " + "; ".join(
        f"a {b.place} ({cost_text(b.cost)}): {b.why}" for b in BUILDINGS) + "."


def system_prompt(v: NpcView) -> str:
    traits = v.genome.traits()
    hints = "; ".join(f"{t}: {TRAIT_HINTS[t]}" for t in traits)
    return (f"You are {v.name}, one of a handful of people making a life in a wild land. "
            f"Your traits are [{', '.join(traits)}]. Act accordingly. ({hints}.)")


def action_prompt(v: NpcView, names: dict[int, str]) -> list[dict]:
    s = v.stats
    payload = {
        "you": {"name": v.name, "health": s["health"], "fullness": s["hunger"],
                "energy": s["energy"], "social_urge": s["social"], "age": years(s["age"]),
                **abilities(s),
                "life_stage": "elder (cannot have children)" if v.elder else
                              ("child" if s["age"] < MATURE_AGE else "adult"),
                "inventory": dict(s.get("inv", {})),
                "can_carry_at_most": s.get("caps") or {i.key: i.cap for i in ITEMS.values() if i.cap},
                **({"standing_on": v.ground} if v.ground else {})},
        "home": "none - you have no hut of your own" if v.home is None else f"your hut is about {v.home} tiles away",
        "rules": "Stats are 0-100. Fullness drops over time; at 0 you starve. Energy at 0 hurts you. "
                 + building_rules() + " A felled tree drops its wood on the ground: walk over it to pick "
                 "it up before someone else does. What you do, you get better at: practice raises your "
                 "skills and slowly your attributes, for life. Everyone ages and dies one day.",
        "buildings_within_5_tiles": v.nearby or {"hut": v.huts_nearby},
        "valid_actions": {str(i): describe_action(a, names, s) for i, a in enumerate(v.actions)},
    }
    return [
        {"role": "system", "content": system_prompt(v)},
        {"role": "user", "content": json.dumps(payload) +
         "\nReply with only the number of the action you take."},
    ]


# Stiff, assistant-like or speech-making phrases: a line containing one is re-rolled.
BANNED = ("hello there", "hi there", "greetings", "how are you", "nice to meet", "good day", "my friend",
          "work together", "we must survive", "the land is hard", "indeed", "shall", "behold", "alas",
          "thee", "thou", "perhaps we", "i must say", "let us", "fellow villager", "my dear")

# How villagers actually talk: short, casual, about what is going on. Shown to the
# model as the register to aim for (a few at a time, never to be copied).
CASUAL = (
    "Ugh, my back. Been chopping all morning.",
    "You eaten yet? I've got a few berries left.",
    "Hey, did you take my wood? Be honest.",
    "Not now, I'm starving.",
    "Nice hut. Bit crooked though.",
    "Can't sleep, the fire keeps going out.",
    "Seriously, again? Leave me alone.",
    "Okay, okay, fine. Two wood, that's it.",
    "Man, I'm wiped. Long day.",
    "You look rough. You sleeping okay?",
    "Ha, you wish.",
    "Whatever, I'm gonna go find food.",
    "Wait, where'd you get that spear?",
    "Don't tell anyone, but I think it's gonna be a bad winter.",
    "Oh come on, that was my bush.",
    "You're alright, you know that?",
    "I dunno, my stomach's been weird all day.",
    "So what, you're just gonna stand there?",
)


def current_mood(stats: dict) -> str:
    """How an NPC feels right now, in a word or two (it colours what they say)."""
    if stats["health"] < 40:
        return "hurt and in pain"
    if stats["hunger"] < 25:
        return "starving"
    if stats["energy"] < 20:
        return "exhausted"
    if stats["hunger"] < 50:
        return "hungry"
    if stats["energy"] < 40:
        return "tired"
    if stats.get("expecting"):
        return "expecting a child, a bit nervous"
    if stats["social"] > 70:
        return "in the mood for a chat"
    return "fine"


def tone_examples(v: NpcView, k: int = 3) -> list[str]:
    """A few everyday lines showing how people here talk (a different handful
    for each villager, so the model has nothing fixed to parrot)."""
    return random.Random(v.id * 7919 + v.stats.get("age", 0)).sample(CASUAL, k)


def dialogue_system_prompt(v: NpcView) -> str:
    hints = "; ".join(TRAIT_HINTS[t] for t in v.genome.traits())
    stage = "an elder" if v.elder else ("a kid" if v.stats.get("age", MATURE_AGE) < MATURE_AGE else "a grown-up")
    voice = f" The way you talk: you {voice_text(v.persona)}." if v.persona else ""
    trade = f" Around here you're known as a {role_name(v.stats)}." if role_name(v.stats) else ""
    quirk = (f" One odd thing about you: you {belief_text(v.persona)}. It slips out now and then, "
             "but mostly you talk about ordinary things.") if v.persona else ""
    return (f"You are {v.name}, {stage} in a small stone-age village. Everybody here knows everybody. "
            f"What you're like: {hints}.{trade}{voice}{quirk}\n"
            "Talk the way a real person talks to someone they see every day: casual, short, plain words, "
            "contractions (I'm, don't, gonna, can't). Say what's on your mind right now: how you feel, food, "
            "wood, your hut, sleep, what they did, something you heard. React to them and to what just happened. "
            "No speeches, no poetry, no riddles, no fancy or old-fashioned words, no announcing who you are. "
            "Never describe what you are doing; just say the words you'd say out loud. "
            "This is how people here sound (don't copy these): "
            + " / ".join(f'"{x}"' for x in tone_examples(v)))


def gossips(ix: Interaction) -> bool:
    """Whether the other one's odd belief is on the speaker's mind this time.
    Mostly it is not: shown every time, it was all anyone talked about."""
    return (ix.me.id * 7 + ix.other.id * 13 + ix.me.stats.get("age", 0) // 20) % 4 == 0


def _situation(ix: Interaction) -> dict:
    mood = "no strong feelings"
    if ix.grudge <= -2:
        mood = "you hate them"
    elif ix.grudge >= 2:
        mood = "you are fond of them"
    them = {"name": ix.other.name, "health": ix.other.stats["health"],
            "looks": current_mood(ix.other.stats),
            "carrying": dict(ix.other.stats.get("inv", {})) or "nothing", "age": years(ix.other.stats["age"])}
    if role_name(ix.other.stats):
        them["known_as"] = role_name(ix.other.stats)
    if ix.other.persona and gossips(ix):
        them["rumour_about_them"] = f"they {belief_text(ix.other.persona)}"
    return {
        "you": {"right_now_you_feel": current_mood(ix.me.stats),
                "health": ix.me.stats["health"], "fullness": ix.me.stats["hunger"],
                "carrying": dict(ix.me.stats.get("inv", {})) or "nothing",
                "social_urge": ix.me.stats["social"]},
        "them": them,
        "your_memories_of_them": ix.memories or ["You have never spoken."],
        "village_news": ix.news[-3:] or ["Nothing has happened yet."],
        "feeling": mood,
    }


# What each interaction option means, shown to the model next to the bare option name.
# Without these, small models only ever pick "talk" or "fight": a bare "mate" never got chosen,
# so LLM-driven villages had no births and died out of old age.
def base_option(option: str) -> str:
    """"trade:wood:food" -> "trade"."""
    return option.split(":")[0]


def they_want(context: str) -> str:
    """What the speaker was after, told to the listener. ``context`` is the
    option, or "refused_<option>" when the listener just turned it down."""
    if context.startswith("refused_"):
        d = INTERACTIONS.get(base_option(context[len("refused_"):]))
        return d.refused if d and d.refused else context
    return option_text(context, mine=False)


def interaction_prompt(ix: Interaction) -> list[dict]:
    payload = {**_situation(ix), "options": {o: option_text(o) for o in ix.options}}
    return [
        {"role": "system", "content": dialogue_system_prompt(ix.me)},
        {"role": "user", "content": json.dumps(payload) +
         f"\nPick one option and say one short, casual line to {ix.other.name}, the way you'd really "
         f"say it out loud (under {MAX_WORDS} words). Format: option | line"},
    ]


def reply_prompt(ix: Interaction, heard: str, option: str) -> list[dict]:
    """``ix.me`` is the one answering; ``ix.other`` just spoke to them."""
    payload = {**_situation(ix), "they_said": heard,
               "they_want": they_want(option)}
    return [
        {"role": "system", "content": dialogue_system_prompt(ix.me)},
        {"role": "user", "content": json.dumps(payload) +
         f"\nAnswer {ix.other.name} with one short, casual line, the way you'd really say it out loud "
         f"(under {MAX_WORDS} words). React to what they actually said."},
    ]


def chat_prompt(ix: Interaction, history: list[tuple[str, str]], json_turns: bool = False) -> list[dict]:
    """``ix.me`` is chatting with ``ix.other`` and it is their turn. ``history`` is the
    conversation so far as (name, line), oldest first; the last line is the other's.

    The conversation goes in as real chat turns (their lines as the user, ours as
    the assistant): small models follow a back-and-forth far better that way than
    from a list inside one message. ``json_turns`` writes our own past turns in the
    JSON shape the answer must take (for backends that force JSON output)."""
    me, other = ix.me, ix.other
    them = f"{other.name}, {'an elder' if other.elder else 'a kid' if other.stats.get('age', 99) < MATURE_AGE else 'a grown-up'}"
    if role_name(other.stats):
        them += f", known as a {role_name(other.stats)}"
    mood = {"fine": "", "in the mood for a chat": " You're in the mood for a chat."}.get(
        current_mood(me.stats), f" Right now you're {current_mood(me.stats)}.")
    feeling = "You can't stand them." if ix.grudge <= -2 else "You like them." if ix.grudge >= 2 else ""
    known = (" What you remember about them: " + " ".join(ix.memories[-3:])) if ix.memories else ""
    system = (dialogue_system_prompt(me) +
              f"\nYou're talking face to face with {them}.{mood} {feeling}{known}\n"
              "Have a real conversation: answer what they actually just said (if they ask something, answer it), "
              "say something new each time, ask them things back. Never repeat a line you already said. "
              f"Keep each line short and casual (under {MAX_WORDS} words). "
              "When you want to end it (bored, busy, hungry, tired, upset, or it has run its course), say a "
              "goodbye and choose leave; otherwise choose stay.")
    messages = [{"role": "system", "content": system}]
    for who, said in history:
        if who == me.name:
            content = json.dumps({"line": said, "leave": False}) if json_turns else f"stay | {said}"
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": said})
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": "(says nothing, waits for you)"})
    return messages


def sky_prompt(v: NpcView, heard: str, memories: list[str], news: list[str]) -> list[dict]:
    """A voice from the sky (the player, as a god) speaks to ``v``, who must answer."""
    payload = {
        "you": {"right_now_you_feel": current_mood(v.stats), "health": v.stats["health"],
                "carrying": dict(v.stats.get("inv", {})) or "nothing"},
        "a_voice_from_the_sky_says": heard,
        "what_the_voice_said_before": memories or ["It has never spoken to you before."],
        "village_news": news[-3:] or ["Nothing has happened yet."],
    }
    return [
        {"role": "system", "content": dialogue_system_prompt(v) +
         "\nOut of nowhere, a voice speaks to you from the sky. Nobody else seems to hear it."},
        {"role": "user", "content": json.dumps(payload) +
         f"\nAnswer the voice out loud with one short line in your own way (under {MAX_WORDS} words). "
         "React to what it actually said: you might be scared, awed, rude, suspicious or thrilled."},
    ]


def consent_prompt(ix: Interaction, proposal: str, line: str) -> list[dict]:
    payload = {
        "proposal": f"{ix.other.name} wants {option_text(proposal, mine=False)} and says: \"{line}\"",
        "memories_of_them": ix.memories or ["You have never met."],
        "you": {"health": ix.me.stats["health"], "fullness": ix.me.stats["hunger"],
                "social_urge": ix.me.stats["social"]},
    }
    return [
        {"role": "system", "content": system_prompt(ix.me)},
        {"role": "user", "content": json.dumps(payload) + "\nDo you agree? Answer yes or no."},
    ]


# --- Grammars ------------------------------------------------------------------

def _alt(values) -> str:
    return " | ".join(json.dumps(str(v)) for v in values)


def action_grammar(n_actions: int) -> str:
    return f"root ::= {_alt(range(n_actions))}\n"


def interaction_grammar(options: list[str]) -> str:
    return (f"root ::= opt \" | \" word (\" \" word){{0,{MAX_WORDS - 1}}}\n"
            f"opt ::= {_alt(options)}\n"
            "word ::= [A-Za-z',.!?-]{1,16}\n")


CONSENT_GRAMMAR = 'root ::= "yes" | "no"\n'
REPLY_GRAMMAR = (f"root ::= word (\" \" word){{0,{MAX_WORDS - 1}}}\n"
                 "word ::= [A-Za-z',.!?-]{1,16}\n")
CHAT_GRAMMAR = (f"root ::= (\"stay\" | \"leave\") \" | \" word (\" \" word){{0,{MAX_WORDS - 1}}}\n"
                "word ::= [A-Za-z',.!?-]{1,16}\n")


def _trigrams(text: str) -> set[tuple[str, ...]]:
    w = re.findall(r"[a-z']+", text.lower())
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


_EXAMPLE_TRIGRAMS = [_trigrams(line) for _, line in BELIEFS]
_BANNED_RE = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in BANNED) + r")\b")
_CASUAL_TRIGRAMS =set().union(*(_trigrams(line) for line in CASUAL))


MIN_CHAT_LINES = 3  # lines someone says in a conversation before they may choose to leave it


def repeats(line: str, earlier: list[str]) -> bool:
    """Whether a line says again what one of ``earlier`` already said."""
    tri, low = _trigrams(line), line.strip().lower()
    return any(low == e.strip().lower() or (tri and len(tri & _trigrams(e)) * 2 >= len(tri)) for e in earlier)


class Canned(str):
    """A pre-written line (heuristic brain), as opposed to one the LLM wrote."""


def is_junk(line: str) -> bool:
    """Output that isn't speech: empty, "...", stray JSON/markup, or no real words
    (1B models do this). It earns a re-roll, then a canned line."""
    t = line.strip()
    return (not re.search(r"[A-Za-z]{2,}", t) or t[:1] in "{[<" or '":' in t
            or "rumour_about" in t or "they_said" in t)


def spoken(line: str) -> str:
    """The model's line, cleaned; raises (so the caller falls back) if it's junk."""
    line = line.strip().lstrip("/-*").strip().strip('"').strip()
    line = re.split(r'"\s*[},\]]|[{}\[\]]', line)[0].strip()  # drop stray JSON a small model tacked on
    line = clamp_words(line)
    if is_junk(line):
        raise ValueError(f"junk line {line!r}")
    return line


def is_generic(line: str, own_belief: int = -1) -> bool:
    """Bland small talk, or a line parroted from the tone examples (small
    models copy them); either earns a re-roll. An NPC may quote its own belief."""
    low = line.lower()
    if is_junk(line) or _BANNED_RE.search(low):
        return True
    tri = _trigrams(line)
    return bool(tri & _CASUAL_TRIGRAMS) or any(tri & ex for i, ex in enumerate(_EXAMPLE_TRIGRAMS) if i != own_belief)


def clamp_words(text: str, n: int = MAX_WORDS) -> str:
    """Cap at ``n`` words. If that cuts a sentence, end at the last complete
    sentence instead when there is one (``"Hi. Who are you"`` -> ``"Hi."``)."""
    words = text.split()
    if len(words) <= n:
        return " ".join(words)
    words = words[:n]
    for i in range(len(words) - 1, -1, -1):
        if words[i][-1] in ".!?":
            return " ".join(words[:i + 1])
    return " ".join(words) + "…"


# --- Heuristic brain -------------------------------------------------------------

LINES = {
    "talk": {
        "Pacifist": ["You eaten yet? I've got extra.", "Hey, you okay? You look tired.", "Nice day, huh. Quiet.",
                     "Careful with that axe, yeah?", "Come sit by the fire later."],
        "Neutral": ["Hey. Find any berries?", "Long day. My feet hurt.", "You seen any good trees around?",
                    "So, what's new with you?", "Might rain. Or not. Who knows."],
        "Hostile": ["What do you want?", "That's my spot. Move.", "Quit staring at me.",
                    "You again. Great.", "Don't touch my stuff."],
        "Bloodthirsty": ["You look kinda weak today.", "Nice wood. Be a shame if I took it.",
                         "Keep walking.", "What are you looking at?"],
    },
    "fight": {
        "Pacifist": ["Sorry, you left me no choice!", "I really didn't want this!"],
        "Neutral": ["You asked for it!", "Okay, that's it!", "I warned you!"],
        "Hostile": ["Get lost!", "I told you to back off!", "You want some? Come on!"],
        "Bloodthirsty": ["That wood's mine now!", "Ha! Come here!", "This is gonna hurt!"],
    },
    "trade": {"*": ["Wood for berries? Fair's fair.", "Wanna swap? I'm short on food.", "Got wood, need food. Deal?",
                    "Trade you. Come on, it's a good deal."]},
    "give": {"*": ["Here, take this. You need it more.", "Go on, it's yours. Don't make it weird.",
                   "Take it. You look like you could use it."]},
    "mate": {"*": ["So... you and me. A kid. Thoughts?", "I like you, you know. Let's have a family.",
                   "We'd make a cute kid. Just saying.", "Stay with me. I mean it."]},
}
MOOD_LINES = {  # said instead when something is bothering them
    "starving": ["I'm so hungry I can't think.", "Got any food? Anything?", "My stomach's killing me."],
    "hungry": ["I could eat. You got anything?", "Is it just me or is food getting scarce?"],
    "exhausted": ["I'm dead on my feet.", "Can't talk. Need sleep."],
    "tired": ["Man, I'm wiped.", "I need a nap. Badly."],
    "hurt and in pain": ["Ow. Everything hurts.", "I'm bleeding here, give me a minute."],
}
REPLIES = {
    "friendly": ["Ha, yeah. Tell me about it.", "Oh, really? Huh.", "Yeah, same here.", "Sure, why not.",
                 "You're not wrong.", "Mm. Could be worse."],
    "cold": ["Not you again.", "Yeah, whatever.", "I remember what you did.", "Go bother someone else."],
    "odd": ["Uh... okay?", "What? Say that again.", "You're weird, you know that?", "...why are you telling me this?"],
    "yes": ["Yeah, okay. Deal.", "Alright, let's do it.", "Fine by me."],
    "thanks": ["Oh. Thanks, really.", "You sure? Okay. Thanks.", "Huh. I owe you one."],
    "bye": ["Anyway, I gotta go.", "Right. I'm off.", "Later.", "Okay, enough talk. Bye.",
            "I've got stuff to do. See you."],
    "busy_bye": ["Sorry, I really need to eat.", "I'm dead on my feet. Gotta sleep.", "Can't talk now, I'm starving."],
    "sky": ["Who said that?!", "Okay... I'm hearing voices now. Great.", "Is that you, sky? What do you want?",
            "I'm not listening. La la la.", "Fine, fine. I heard you.", "Please don't smite me."],
    "no_trade": ["Nah, I'm good.", "No deal.", "Not today."],
    "no_mate": ["Uh, no. Sorry.", "Ha, not a chance.", "Not with you."],
}
DEFAULT_LINES = ["Hm.", "You. Yes, you.", "Listen to me."]


# How much an NPC wants each kind of action: SCORERS[kind](view, action) -> score.
# Every action kind in the content registry needs one (checked below).
SCORERS: dict = {}


def scorer(kind):
    def reg(fn):
        SCORERS[kind] = fn
        return fn
    return reg


@scorer("sleep")
def _(v, a):
    e = v.stats["energy"]
    return (100 if e == 0 else (85 if e < 25 else 4)) + (12 if v.genome.work == "Lazy" else 0)


@scorer("eat")
def _(v, a):
    h = v.stats["hunger"]
    return 80 + (50 - h) if h < 55 else 0


def knack(stats: dict, skill: str = "", attr: str = "") -> float:
    """A nudge toward what someone is good at: so the strong end up chopping,
    the clever building, and practice makes a habit."""
    return (2.0 * skill_of(stats, skill) if skill else 0) + (2.0 * (attr_of(stats, attr) - ATTR_AVERAGE) if attr else 0)


@scorer("forage")
def _(v, a):
    wants = 50 + (20 if v.stats["hunger"] < 60 else 0) if have(v.stats, "food") < 4 else 5
    return wants + (knack(v.stats, "foraging", "agility") if have(v.stats, "food") < 10 else 0)


@scorer("chop")
def _(v, a):
    return (35 if have(v.stats, "wood") < 12 else 5) + {"Industrious": 15, "Visionary": 10, "Lazy": -20}.get(
        v.genome.work, 0) + (knack(v.stats, "woodcutting", "strength") if have(v.stats, "wood") < 20 else 0)


def _build_hut(v, near):
    if v.home is None:  # everyone wants a home of their own
        return 60 + (15 if v.genome.work == "Visionary" else 0)
    return 40 if v.genome.work == "Visionary" and near.get("hut", 0) < 4 else 2


def _build_farm(v, near):
    return {"Industrious": 45, "Visionary": 35, "Lazy": 10}.get(v.genome.work, 20) - 25 * near.get("farm", 0)


def _build_fire(v, near):
    return (30 + (15 if v.genome.social == "Charismatic" else 0)) if near.get("fire", 0) == 0 else 2


# Buildings without an entry get a mild "worth having one nearby" score.
BUILD_SCORERS = {"hut": _build_hut, "farm": _build_farm, "fire": _build_fire}


@scorer("build")
def _(v, a):
    near = v.nearby or {"hut": v.huts_nearby}
    fn = BUILD_SCORERS.get(a["what"])
    score = fn(v, near) if fn else (20 if near.get(a["what"], 0) == 0 else 2)
    spec = OBJECTS.get(a["what"])
    if score > 0 and spec is not None and spec.regrow and "soil" in a:
        score *= min(1.3, max(0.3, a["soil"]))  # no sense planting where little grows
    return score + (knack(v.stats, "building", "intelligence") if score > 5 else 0)


@scorer("craft")
def _(v, a):
    if a["what"] == "spear":
        return (60 if v.genome.aggression in ("Hostile", "Bloodthirsty") else 12) + knack(v.stats, "crafting")
    return 15 + knack(v.stats, "crafting", "intelligence")


def _goto_tree(v, a):
    wants = 30 + (20 if v.genome.work == "Industrious" else 0) if have(v.stats, "wood") < 15 else 5
    return wants + (knack(v.stats, "woodcutting", "strength") if wants > 5 else 0) - a["dist"] / 2


def _goto_bush(v, a):
    return (70 if v.stats["hunger"] < 60 and have(v.stats, "food") == 0 else 20) - a["dist"]


def _goto_farm(v, a):
    return (75 if v.stats["hunger"] < 60 and have(v.stats, "food") == 0 else 15) - a["dist"]


def _goto_fire(v, a):
    score = (25 if have(v.stats, "food") > 0 and v.stats["hunger"] < 60 else 4) - a["dist"] / 2
    return score + (10 if v.genome.social == "Charismatic" else 0)


def _goto_shelter(v, a):
    return (55 if v.stats["energy"] < 35 else 3) - a["dist"]


GOTO_SCORERS = {"tree": _goto_tree, "bush": _goto_bush, "farm": _goto_farm, "fire": _goto_fire,
                "hut": _goto_shelter}


@scorer("collect")
def _(v, a):
    # things lying on the ground are free for the taking: worth a short walk
    s, items = v.stats, a["items"]
    score = 30.0
    if "food" in items:
        score = 78 if s["hunger"] < 60 and have(s, "food") == 0 else 45
    if "wood" in items and have(s, "wood") < 16:
        score = max(score, 58 + {"Industrious": 10, "Visionary": 8}.get(v.genome.work, 0))
    if "spear" in items:
        score = max(score, 65 if v.genome.aggression in ("Hostile", "Bloodthirsty") else 35)
    if a.get("owner", -1) not in (-1, v.id):
        # it is somebody's: most people leave it be, unless they are desperate or that sort
        desperate = "food" in items and s["hunger"] < 25
        score -= 0 if desperate else {"Pacifist": 60, "Neutral": 45, "Hostile": 15, "Bloodthirsty": 0}[v.genome.aggression]
    return score - a["dist"] * 1.5


@scorer("goto")
def _(v, a):
    fn = GOTO_SCORERS.get(a["what"])
    if fn:
        return fn(v, a)
    spec = OBJECTS.get(a["what"])
    if spec and spec.food:           # somewhere to find food
        return _goto_bush(v, a)
    if spec and spec.sleep_energy:   # somewhere to rest
        return _goto_shelter(v, a)
    return 5 - a["dist"] / 2


@scorer("goto_npc")
def _(v, a):
    s, g = v.stats, v.genome
    score = {"Charismatic": 45, "Loner": 0, "Paranoid": 10}.get(g.social, 20) + s["social"] / 4
    score += 12 if g.aggression == "Bloodthirsty" else 0
    # adults with the urge go looking for a partner
    if MATURE_AGE <= s["age"] and not v.elder and s["social"] >= 30 and s["hunger"] >= 50:
        score += 25
    return score - a["dist"] / 2


@scorer("interact")
def _(v, a):
    g = v.genome
    score = 30 + v.stats["social"] / 3
    score += {"Charismatic": 15, "Loner": -25, "Paranoid": -10}.get(g.social, 0)
    score += 15 if g.aggression == "Bloodthirsty" else 0
    score += knack(v.stats, "speech", "charisma") / 2
    return score + (25 if "mate" in a["options"] else 0)


@scorer("wander")
def _(v, a):
    return 12


@scorer("step")
def _(v, a):
    return -1000  # (the player's to take, never a brain's)


_unscored = [k for k in ACTIONS if k not in SCORERS]
assert not _unscored, f"action kinds without a heuristic scorer: {_unscored}"


class HeuristicBrain:
    name = "heuristic"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    async def choose_action(self, v: NpcView, names: dict[int, str]) -> int:
        return self.pick_action(v)

    def scores(self, v: NpcView) -> list[float]:
        """How much this NPC wants each offered action (no randomness)."""
        return [SCORERS[a["kind"]](v, a) for a in v.actions]

    def pick_action(self, v: NpcView, avoid: frozenset = frozenset()) -> int:
        best, best_score = 0, float("-inf")
        for i, a in enumerate(v.actions):
            if a["kind"] in avoid:
                continue
            score = SCORERS[a["kind"]](v, a) + self.rng.random() * 10
            if score > best_score:
                best, best_score = i, score
        return best

    def shortlist(self, v: NpcView, k: int) -> list[int]:
        """Indices of the ``k`` most appealing actions, in their original order.
        Small models choose badly from long lists, so the LLM is only shown
        these. Sleeping and eating always stay on the list."""
        if len(v.actions) <= k:
            return list(range(len(v.actions)))
        sc = self.scores(v)
        keep = {i for i, a in enumerate(v.actions) if a["kind"] in ("sleep", "eat")}
        for i in sorted(range(len(sc)), key=lambda i: (-sc[i], i)):
            if len(keep) >= k:
                break
            keep.add(i)
        return sorted(keep)

    async def choose_interaction(self, ix: Interaction) -> tuple[str, str]:
        option, line = await self._choose_interaction(ix)
        return option, Canned(line)

    async def reply(self, ix: Interaction, heard: str, option: str) -> str:
        return Canned(await self._reply(ix, heard, option))

    async def answer_sky(self, v: NpcView, heard: str, memories: list[str], news: list[str]) -> str:
        return Canned(self.rng.choice(REPLIES["sky"]))

    async def chat(self, ix: Interaction, history: list[tuple[str, str]]) -> tuple[str, bool]:
        """The next line in a conversation, and whether to walk away after it."""
        feeling = current_mood(ix.me.stats)
        if feeling in ("starving", "exhausted", "hurt and in pain"):
            return Canned(self.rng.choice(REPLIES["busy_bye"])), True
        mine = sum(1 for who, _ in history if who == ix.me.name)
        patience = {"Charismatic": 7, "Loner": 2, "Paranoid": 3}.get(ix.me.genome.social, 4)
        if mine >= patience + self.rng.randrange(3) or (ix.grudge <= -2 and self.rng.random() < 0.4):
            return Canned(self.rng.choice(REPLIES["bye"])), True
        return Canned(await self._reply(ix, history[-1][1] if history else "", "talk")), False

    def option_weight(self, ix: Interaction, option: str) -> float:
        """How much this NPC feels like doing ``option`` with the other one."""
        g, me, them = ix.me.genome, ix.me.stats, ix.other.stats
        key, *args = option.split(":")
        if key == "talk":
            return 30.0
        if key == "fight":
            if g.aggression == "Pacifist":
                return -100.0
            # (those who can fight are readier to; the weak think twice about the strong)
            edge = attr_of(me, "strength") - attr_of(them, "strength") + skill_of(me, "combat") - skill_of(them, "combat")
            return ({"Neutral": 0, "Hostile": 10, "Bloodthirsty": 30}.get(g.aggression, 0) + max(-15, min(10, 2 * edge))
                    + min(30, max(0, -ix.grudge) * 4) + (10 if g.social == "Paranoid" and ix.grudge < 0 else 0)
                    - (25 if me["health"] < 40 else 0))
        if key == "trade":
            if len(args) != 2:
                return 45.0 if have(me, "food") == 0 or have(me, "wood") < 2 else 8.0
            give, get = args
            if give == "food" and (me["hunger"] < 50 or have(me, "food") <= 1):
                return -50.0  # not while hungry
            if get == "food":
                return 50.0 if have(me, "food") == 0 or me["hunger"] < 50 else 6.0
            if get == "wood":
                return 38.0 if have(me, "wood") < 6 else 4.0
            if get == "spear":
                return 45.0 if g.aggression in ("Hostile", "Bloodthirsty") else 8.0
            return 5.0
        if key == "give":
            item = args[0] if args else "food"
            spare = have(me, item) >= {"food": 3, "wood": 8}.get(item, 2)
            if item == "food" and spare and them["hunger"] < 40 and have(them, "food") == 0:
                # feed the hungry: family and friends always, strangers if you are the kind sort
                kind = ix.grudge >= 2 or g.aggression in ("Pacifist", "Neutral")
                return (62.0 if kind else 12.0) - (60 if ix.grudge < -1 else 0)
            if spare and ix.grudge >= 4 and g.social == "Charismatic":
                return 24.0  # a present for a good friend
            return -20.0
        if key == "mate":
            return 55.0 + (20 if g.social == "Charismatic" else 0) - (60 if ix.grudge < -1 else 0)
        return 10.0

    async def _choose_interaction(self, ix: Interaction) -> tuple[str, str]:
        g = ix.me.genome
        scored = {o: self.option_weight(ix, o) + self.rng.random() * 15 for o in ix.options}
        option = max(scored, key=scored.get)
        if option == "talk":
            feeling = current_mood(ix.me.stats)
            if feeling in MOOD_LINES and self.rng.random() < 0.5:
                return option, self.rng.choice(MOOD_LINES[feeling])
            if ix.me.persona and self.rng.random() < 0.15:  # the odd belief slips out now and then
                return option, BELIEFS[ix.me.persona["belief"]][1]
        lines = LINES.get(base_option(option), {})
        pool = lines.get(g.aggression) or lines.get("*") or DEFAULT_LINES
        return option, self.rng.choice(pool)

    async def _reply(self, ix: Interaction, heard: str, option: str) -> str:
        if option.startswith("refused_"):
            return self.rng.choice(REPLIES["no_mate" if option == "refused_mate" else "no_trade"])
        key = base_option(option)
        if key == "give":
            return self.rng.choice(REPLIES["thanks"])
        if key in ("trade", "mate"):
            return self.rng.choice(REPLIES["yes"])
        if ix.grudge <= -2 or key == "fight":
            return self.rng.choice(REPLIES["cold"])
        if ix.me.persona and self.rng.random() < 0.12:
            return BELIEFS[ix.me.persona["belief"]][1]
        return self.rng.choice(REPLIES["friendly" if ix.grudge >= 0 and self.rng.random() < 0.75 else "odd"])

    async def consent(self, ix: Interaction, proposal: str, line: str) -> bool:
        """``ix.me`` is the one being asked; ``proposal`` is the other one's option."""
        g, me = ix.me.genome, ix.me.stats
        if ix.grudge < -1:
            return False
        key, *args = proposal.split(":")
        if key == "mate":
            # (the engine already checked age, health and both social urges)
            # (a charming suitor wins over even the solitary)
            charm = 0.05 * (attr_of(ix.other.stats, "charisma") - ATTR_AVERAGE)
            return not (g.social == "Loner" and self.rng.random() < 0.6 - charm)
        if key == "trade" and len(args) == 2 and args[1] == "food" and (me["hunger"] < 50 or have(me, "food") <= 1):
            return False  # they want my food and I need it myself
        if g.social == "Paranoid" and self.rng.random() < 0.5:
            return False
        return True

# --- llama.cpp brain -------------------------------------------------------------

class LlamaBrain:
    """Talks to ``llama-server`` (llama.cpp). Start it with e.g.

        llama-server -m bonsai-1.7b.gguf --port 8080 --parallel 8 -c 8192

    ``--parallel`` lets the server batch the concurrent per-NPC requests.
    """
    name = "llm"

    def __init__(self, url: str, fallback: HeuristicBrain, timeout: float = 30.0,
                 temperature: float = 0.7, concurrency: int = 8, talk_temperature: float = 1.05):
        import httpx

        self.url = url.rstrip("/")
        self.fallback = fallback
        self.temperature = temperature
        self.talk_temperature = talk_temperature  # speech runs hotter than decisions
        self._client = httpx.AsyncClient(timeout=timeout)
        self._sem = asyncio.Semaphore(concurrency)
        self.failures = 0
        self.calls = 0

    async def healthy(self) -> bool:
        try:
            r = await self._client.get(f"{self.url}/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    async def _complete(self, messages: list[dict], grammar: str, max_tokens: int,
                        temperature: float | None = None) -> str:
        talk = temperature is not None
        async with self._sem:
            self.calls += 1
            r = await self._client.post(f"{self.url}/v1/chat/completions", json={
                "messages": messages,
                "grammar": grammar,
                "max_tokens": max_tokens,
                "temperature": self.temperature if temperature is None else temperature,
                **({"repeat_penalty": 1.2, "presence_penalty": 0.6, "min_p": 0.05} if talk else {}),
                "cache_prompt": True,
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    def _failed(self, what: str, e: Exception):
        self.failures += 1
        if self.failures <= 3 or self.failures % 50 == 0:
            log.warning("LLM %s failed (%d total), using heuristic: %r", what, self.failures, e)

    async def choose_action(self, v: NpcView, names: dict[int, str]) -> int:
        try:
            out = await self._complete(action_prompt(v, names), action_grammar(len(v.actions)), 4)
            idx = int(re.match(r"\d+", out).group())
            if 0 <= idx < len(v.actions):
                return idx
            raise ValueError(f"index {idx} out of range")
        except Exception as e:
            self._failed("action", e)
            return self.fallback.pick_action(v)

    async def choose_interaction(self, ix: Interaction) -> tuple[str, str]:
        try:
            for _ in range(2):  # one retry if the line comes out bland
                out = await self._complete(interaction_prompt(ix), interaction_grammar(ix.options), 48,
                                           self.talk_temperature)
                option, _, line = out.partition("|")
                option = option.strip()
                if option not in ix.options:
                    raise ValueError(f"bad option {option!r}")
                if not is_generic(line, ix.me.persona.get("belief", -1)):
                    break
            return option, spoken(line)
        except Exception as e:
            self._failed("interaction", e)
            return await self.fallback.choose_interaction(ix)

    async def reply(self, ix: Interaction, heard: str, option: str) -> str:
        try:
            for _ in range(2):
                out = await self._complete(reply_prompt(ix, heard, option), REPLY_GRAMMAR, 40, self.talk_temperature)
                if not is_generic(out, ix.me.persona.get("belief", -1)):
                    break
            return spoken(out)
        except Exception as e:
            self._failed("reply", e)
            return await self.fallback.reply(ix, heard, option)

    async def _line(self, messages: list[dict]) -> str:
        """One spoken line, free-form (under MAX_WORDS words)."""
        return await self._complete(messages, REPLY_GRAMMAR, 40, self.talk_temperature)

    async def _chat_line(self, messages: list[dict]) -> tuple[str, bool]:
        out = await self._complete(messages, CHAT_GRAMMAR, 48, self.talk_temperature)
        choice, _, line = out.partition("|")
        return line, choice.strip() == "leave"

    json_turns = False  # (see chat_prompt)

    async def chat(self, ix: Interaction, history: list[tuple[str, str]]) -> tuple[str, bool]:
        mine = [said for who, said in history if who == ix.me.name]
        try:
            for _ in range(3):  # re-roll a line that comes out bland, or that they already said
                line, leave = await self._chat_line(chat_prompt(ix, history, self.json_turns))
                if not is_generic(line, ix.me.persona.get("belief", -1)) and not repeats(line, mine):
                    break
            # Small models choose "leave" far too eagerly: someone who has only just started
            # talking stays, unless something is really wrong (starving, hurt, a grudge).
            urgent = current_mood(ix.me.stats) in ("starving", "exhausted", "hurt and in pain") or ix.grudge <= -2
            return spoken(line), leave and (urgent or len(mine) >= MIN_CHAT_LINES)
        except Exception as e:
            self._failed("chat", e)
            return await self.fallback.chat(ix, history)

    async def answer_sky(self, v: NpcView, heard: str, memories: list[str], news: list[str]) -> str:
        try:
            for _ in range(2):
                out = await self._line(sky_prompt(v, heard, memories, news))
                if not is_generic(out, v.persona.get("belief", -1)):
                    break
            return spoken(out)
        except Exception as e:
            self._failed("answer to the sky", e)
            return await self.fallback.answer_sky(v, heard, memories, news)

    async def consent(self, ix: Interaction, proposal: str, line: str) -> bool:
        try:
            out = await self._complete(consent_prompt(ix, proposal, line), CONSENT_GRAMMAR, 2)
            return out.lower().startswith("y")
        except Exception as e:
            self._failed("consent", e)
            return await self.fallback.consent(ix, proposal, line)

    async def close(self):
        await self._client.aclose()


class OllamaBrain(LlamaBrain):
    """Talks to Ollama's native /api/chat. Ollama has no GBNF parameter, but
    its ``format`` field takes a JSON schema that Ollama compiles into a
    grammar, so the same guarantee holds: ``action`` is an enum of exactly the
    indices the engine offered and ``option`` an enum of the engine's options.
    """
    name = "ollama"

    def __init__(self, url: str, model: str, fallback: HeuristicBrain, timeout: float = 90.0,
                 temperature: float = 0.7, concurrency: int = 4, talk_temperature: float = 1.05):
        super().__init__(url, fallback, timeout, temperature, concurrency, talk_temperature)
        self.model = model
        self.name = f"ollama:{model}"

    async def server_up(self) -> bool:
        try:
            return (await self._client.get(f"{self.url}/api/version", timeout=3)).status_code == 200
        except Exception:
            return False

    async def ensure_running(self, wait: float = 20.0) -> bool:
        """Start ``ollama serve`` if nothing answers (Ollama's tray app normally
        runs it already)."""
        if await self.server_up():
            return True
        import shutil
        import subprocess
        import sys

        exe = shutil.which("ollama")
        if not exe:
            return False
        log.info("Ollama not running; starting `ollama serve`")
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=sys.platform != "win32")
        for _ in range(int(wait * 2)):
            await asyncio.sleep(0.5)
            if await self.server_up():
                return True
        return False

    async def healthy(self) -> bool:
        try:
            r = await self._client.get(f"{self.url}/api/tags", timeout=3)
            names = {m["name"] for m in r.json().get("models", [])}
            return self.model in names or f"{self.model}:latest" in names
        except Exception:
            return False

    async def warm_up(self, timeout: float = 180.0):
        """Load the model into memory now so the first tick isn't a cold start."""
        log.info("Loading %s into Ollama (first load can take a minute)...", self.model)
        r = await self._client.post(f"{self.url}/api/generate", timeout=timeout,
                                    json={"model": self.model, "keep_alive": "30m"})
        r.raise_for_status()
        log.info("%s ready", self.model)

    async def _chat(self, messages: list[dict], schema: dict, max_tokens: int,
                    temperature: float | None = None) -> dict:
        opts = {"temperature": self.temperature if temperature is None else temperature, "num_predict": max_tokens}
        if temperature is not None:
            opts.update(repeat_penalty=1.2, presence_penalty=0.6, min_p=0.05)
        async with self._sem:
            self.calls += 1
            r = await self._client.post(f"{self.url}/api/chat", json={
                "model": self.model,
                "messages": messages,
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": opts,
            })
            r.raise_for_status()
            return json.loads(r.json()["message"]["content"])

    async def choose_action(self, v: NpcView, names: dict[int, str]) -> int:
        schema = {"type": "object", "required": ["action"],
                  "properties": {"action": {"type": "integer", "enum": list(range(len(v.actions)))}}}
        try:
            idx = int((await self._chat(action_prompt(v, names), schema, 16))["action"])
            if 0 <= idx < len(v.actions):
                return idx
            raise ValueError(f"index {idx} out of range")
        except Exception as e:
            self._failed("action", e)
            return self.fallback.pick_action(v)

    async def choose_interaction(self, ix: Interaction) -> tuple[str, str]:
        schema = {"type": "object", "required": ["option", "line"],
                  "properties": {"option": {"type": "string", "enum": ix.options},
                                 "line": {"type": "string"}}}
        try:
            for _ in range(2):  # one retry if the line comes out bland
                out = await self._chat(interaction_prompt(ix), schema, 64, self.talk_temperature)
                if out["option"] not in ix.options:
                    raise ValueError(f"bad option {out['option']!r}")
                if not is_generic(str(out["line"]), ix.me.persona.get("belief", -1)):
                    break
            return out["option"], spoken(str(out["line"]))
        except Exception as e:
            self._failed("interaction", e)
            return await self.fallback.choose_interaction(ix)

    async def reply(self, ix: Interaction, heard: str, option: str) -> str:
        schema = {"type": "object", "required": ["line"], "properties": {"line": {"type": "string"}}}
        try:
            for _ in range(2):
                out = await self._chat(reply_prompt(ix, heard, option), schema, 56, self.talk_temperature)
                if not is_generic(str(out["line"]), ix.me.persona.get("belief", -1)):
                    break
            return spoken(str(out["line"]))
        except Exception as e:
            self._failed("reply", e)
            return await self.fallback.reply(ix, heard, option)

    json_turns = True  # (Ollama forces JSON answers: show our past turns in that shape too)

    async def _chat_line(self, messages: list[dict]) -> tuple[str, bool]:
        schema = {"type": "object", "required": ["line", "leave"],
                  "properties": {"line": {"type": "string"}, "leave": {"type": "boolean"}}}
        out = await self._chat(messages, schema, 72, self.talk_temperature)
        return str(out["line"]), bool(out["leave"])

    async def _line(self, messages: list[dict]) -> str:
        schema = {"type": "object", "required": ["line"], "properties": {"line": {"type": "string"}}}
        return str((await self._chat(messages, schema, 56, self.talk_temperature))["line"])

    async def consent(self, ix: Interaction, proposal: str, line: str) -> bool:
        schema = {"type": "object", "required": ["agree"], "properties": {"agree": {"type": "boolean"}}}
        try:
            # room for whitespace the model may emit around {"agree": true}
            return bool((await self._chat(consent_prompt(ix, proposal, line), schema, 24))["agree"])
        except Exception as e:
            self._failed("consent", e)
            return await self.fallback.consent(ix, proposal, line)


async def make_brain(kind: str, url: str, seed: int, ollama_url: str = "http://127.0.0.1:11434",
                     model: str = "bonsai-2:2b"):
    """kind: 'auto' (llama.cpp if up, else Ollama if it has `model`, else heuristic),
    'llm' (llama.cpp), 'ollama', or 'heuristic'."""
    heuristic = HeuristicBrain(seed)
    if kind == "heuristic":
        return heuristic

    if kind in ("auto", "llm"):
        brain = LlamaBrain(url, heuristic)
        if await brain.healthy():
            return brain
        if kind == "llm":
            log.warning("llama.cpp server at %s is not answering yet; every call will fall back until it does", url)
            return brain
        await brain.close()

    ob = OllamaBrain(ollama_url, model, heuristic)
    if await ob.ensure_running() and await ob.healthy():
        try:
            await ob.warm_up()
            return ob
        except Exception as e:
            log.warning("Could not load %s in Ollama: %r", model, e)
    elif await ob.server_up():
        log.warning("Ollama is running but has no model %r (try: ollama pull %s)", model, model)
    if kind == "ollama":
        log.warning("Ollama brain unavailable; calls will fall back to the heuristic until it is")
        return ob
    await ob.close()
    log.info("No LLM backend found; using heuristic brain")
    return heuristic
