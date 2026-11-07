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

from .traits import TRAIT_HINTS, Genome

log = logging.getLogger(__name__)

MAX_WORDS = 10


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


@dataclass
class Interaction:
    me: NpcView
    other: NpcView
    options: list[str]
    memories: list[str]
    grudge: int


# --- Prompt assembly ---------------------------------------------------------

# name -> (wood cost, what it is for); costs must match the engine's BUILDINGS
BUILD_INFO = {
    "hut": (10, "becomes your home; best sleep and healing"),
    "farm": (6, "crops that regrow 3 food again and again"),
    "fire": (3, "campfire: cooked meals fill more, warmer sleep, a gathering spot"),
}
PLACE_NAMES = {"tree": "tree", "bush": "berry bush", "hut": "hut", "farm": "farm", "fire": "campfire"}

def describe_action(act: dict, names: dict[int, str], me: dict) -> str:
    k = act["kind"]
    if k == "sleep":
        return "Sleep (restore energy)"
    if k == "eat":
        return f"Eat food (you carry {me['food']})"
    if k == "chop":
        return "Chop the adjacent tree (+4 wood)"
    if k == "forage":
        return "Gather food from the adjacent bush or farm (+2-3 food)"
    if k == "build":
        what = act.get("what", "hut")
        cost, why = BUILD_INFO.get(what, (0, ""))
        return f"Build a {PLACE_NAMES.get(what, what)} here (costs {cost} wood; {why})"
    if k == "craft_spear":
        return "Craft a spear (costs 5 wood, stronger in fights)"
    if k == "interact":
        return f"Interact with {names.get(act['target'], '?')}"
    if k == "goto":
        return f"Walk toward the nearest {PLACE_NAMES.get(act['what'], act['what'])} ({act['dist']} steps)"
    if k == "goto_npc":
        return f"Walk toward {names.get(act['target'], '?')} ({act['dist']} steps)"
    if k == "wander":
        return "Wander randomly"
    return k


def system_prompt(v: NpcView) -> str:
    traits = v.genome.traits()
    hints = "; ".join(f"{t}: {TRAIT_HINTS[t]}" for t in traits)
    return (f"You are {v.name}, an NPC surviving in a harsh, barren world. "
            f"Your traits are [{', '.join(traits)}]. Act accordingly. ({hints}.)")


def action_prompt(v: NpcView, names: dict[int, str]) -> list[dict]:
    s = v.stats
    payload = {
        "you": {"name": v.name, "health": s["health"], "fullness": s["hunger"],
                "energy": s["energy"], "social_urge": s["social"], "age": s["age"],
                "life_stage": "elder (cannot have children)" if v.elder else
                              ("child" if s["age"] < 60 else "adult"),
                "inventory": {"wood": s["wood"], "food": s["food"], "spear": bool(s["spear"])}},
        "home": "none - you have no hut of your own" if v.home is None else f"your hut is about {v.home} tiles away",
        "rules": "Stats are 0-100. Fullness drops over time; at 0 you starve. Energy at 0 hurts you. "
                 "You decide what to build: a hut (10 wood) is a home, a farm (6 wood) feeds you for good, "
                 "a campfire (3 wood) cooks food and draws company. Everyone ages and dies one day.",
        "buildings_within_5_tiles": v.nearby or {"hut": v.huts_nearby},
        "valid_actions": {str(i): describe_action(a, names, s) for i, a in enumerate(v.actions)},
    }
    return [
        {"role": "system", "content": system_prompt(v)},
        {"role": "user", "content": json.dumps(payload) +
         "\nReply with only the number of the action you take."},
    ]


def interaction_prompt(ix: Interaction) -> list[dict]:
    mood = "neutral"
    if ix.grudge <= -2:
        mood = "you hold a grudge against them"
    elif ix.grudge >= 2:
        mood = "you consider them an ally"
    payload = {
        "you": {"health": ix.me.stats["health"], "social_urge": ix.me.stats["social"],
                "spear": bool(ix.me.stats["spear"])},
        "them": {"name": ix.other.name, "health": ix.other.stats["health"],
                 "spear": bool(ix.other.stats["spear"])},
        "memories_of_them": ix.memories or ["You have never met."],
        "feeling": mood,
        "options": ix.options,
    }
    return [
        {"role": "system", "content": system_prompt(ix.me)},
        {"role": "user", "content": json.dumps(payload) +
         f"\nPick one option and say one short sentence to {ix.other.name} (under {MAX_WORDS} words). "
         "Format: option | line"},
    ]


def consent_prompt(ix: Interaction, proposal: str, line: str) -> list[dict]:
    payload = {
        "proposal": f"{ix.other.name} wants to {proposal} with you and says: \"{line}\"",
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
        "Pacifist": ["Peace, friend. The land is hard enough.", "Good to see a kind face."],
        "Neutral": ["Hello there. Found any berries?", "Long day. How are you?"],
        "Hostile": ["What do you want? Keep moving.", "Stay out of my way."],
        "Bloodthirsty": ["You look weak today.", "Watch your back, stranger."],
    },
    "fight": {
        "Pacifist": ["Forgive me, I have no choice!"],
        "Neutral": ["You asked for this!", "This ends now."],
        "Hostile": ["Get away from my land!", "I warned you!"],
        "Bloodthirsty": ["Your wood is mine now!", "Blood for the barren earth!"],
    },
    "trade": {"*": ["Wood for berries? Fair deal.", "Let us trade, neighbour.", "I need food, take wood."]},
    "mate": {"*": ["Let us build a family together.", "Stay with me tonight.", "Our children will thrive here."]},
}


class HeuristicBrain:
    name = "heuristic"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    async def choose_action(self, v: NpcView, names: dict[int, str]) -> int:
        return self.pick_action(v)

    @staticmethod
    def _build_score(v: NpcView, what: str) -> float:
        g, near = v.genome, v.nearby or {"hut": v.huts_nearby}
        if what == "hut":
            if v.home is None:  # everyone wants a home of their own
                return 60 + (15 if g.work == "Visionary" else 0)
            return 40 if g.work == "Visionary" and near.get("hut", 0) < 4 else 2
        if what == "farm":
            score = {"Industrious": 45, "Visionary": 35, "Lazy": 10}.get(g.work, 20)
            return score - 25 * near.get("farm", 0)
        if what == "fire":
            return (30 + (15 if g.social == "Charismatic" else 0)) if near.get("fire", 0) == 0 else 2
        return 0

    def pick_action(self, v: NpcView, avoid: frozenset = frozenset()) -> int:
        s, g = v.stats, v.genome
        best, best_score = 0, float("-inf")
        for i, a in enumerate(v.actions):
            k = a["kind"]
            if k in avoid:
                continue
            score = 0.0
            if k == "sleep":
                score = 100 if s["energy"] == 0 else (85 if s["energy"] < 25 else 4)
                score += 12 if g.work == "Lazy" else 0
            elif k == "eat":
                score = 80 + (50 - s["hunger"]) if s["hunger"] < 55 else 0
            elif k == "forage":
                score = 50 + (20 if s["hunger"] < 60 else 0) if s["food"] < 4 else 5
            elif k == "chop":
                score = 35 if s["wood"] < 12 else 5
                score += {"Industrious": 15, "Visionary": 10, "Lazy": -20}.get(g.work, 0)
            elif k == "build":
                score = self._build_score(v, a.get("what", "hut"))
            elif k == "craft_spear":
                score = 60 if g.aggression in ("Hostile", "Bloodthirsty") else 12
            elif k == "goto":
                d = a["dist"]
                if a["what"] == "bush":
                    score = (70 if s["hunger"] < 60 and s["food"] == 0 else 20) - d
                elif a["what"] == "tree":
                    score = (30 + (20 if g.work == "Industrious" else 0) if s["wood"] < 15 else 5) - d / 2
                elif a["what"] == "farm":
                    score = (75 if s["hunger"] < 60 and s["food"] == 0 else 15) - d
                elif a["what"] == "fire":
                    score = (25 if s["food"] > 0 and s["hunger"] < 60 else 4) - d / 2
                    score += 10 if g.social == "Charismatic" else 0
                else:
                    score = (55 if s["energy"] < 35 else 3) - d
            elif k == "goto_npc":
                score = {"Charismatic": 45, "Loner": 0, "Paranoid": 10}.get(g.social, 20) + s["social"] / 4
                score += 12 if g.aggression == "Bloodthirsty" else 0
                # adults with the urge go looking for a partner (60 = adulthood, see engine)
                if 60 <= s["age"] and not v.elder and s["social"] >= 30 and s["hunger"] >= 50:
                    score += 25
                score -= a["dist"] / 2
            elif k == "interact":
                score = 30 + s["social"] / 3
                score += {"Charismatic": 15, "Loner": -25, "Paranoid": -10}.get(g.social, 0)
                score += 15 if g.aggression == "Bloodthirsty" else 0
                score += 25 if "mate" in a["options"] else 0
            elif k == "wander":
                score = 12
            score += self.rng.random() * 10
            if score > best_score:
                best, best_score = i, score
        return best

    async def choose_interaction(self, ix: Interaction) -> tuple[str, str]:
        g = ix.me.genome
        weights = {
            "talk": 30.0,
            "fight": {"Pacifist": 0, "Neutral": 0, "Hostile": 10, "Bloodthirsty": 30}[g.aggression]
                     + min(30, max(0, -ix.grudge) * 4) + (10 if g.social == "Paranoid" and ix.grudge < 0 else 0)
                     - (25 if ix.me.stats["health"] < 40 else 0),
            "trade": 45.0 if ix.me.stats["food"] == 0 or ix.me.stats["wood"] < 2 else 8.0,
            "mate": 55.0 + (20 if g.social == "Charismatic" else 0) - (60 if ix.grudge < -1 else 0),
        }
        if g.aggression == "Pacifist":
            weights["fight"] = -100
        scored = {o: weights[o] + self.rng.random() * 15 for o in ix.options}
        option = max(scored, key=scored.get)
        pool = LINES[option].get(g.aggression) or LINES[option]["*"]
        return option, self.rng.choice(pool)

    async def consent(self, ix: Interaction, proposal: str, line: str) -> bool:
        g = ix.me.genome
        if ix.grudge < -1:
            return False
        if proposal == "mate":
            # (the engine already checked age, health and both social urges)
            return not (g.social == "Loner" and self.rng.random() < 0.6)
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
                 temperature: float = 0.7, concurrency: int = 8):
        import httpx

        self.url = url.rstrip("/")
        self.fallback = fallback
        self.temperature = temperature
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

    async def _complete(self, messages: list[dict], grammar: str, max_tokens: int) -> str:
        async with self._sem:
            self.calls += 1
            r = await self._client.post(f"{self.url}/v1/chat/completions", json={
                "messages": messages,
                "grammar": grammar,
                "max_tokens": max_tokens,
                "temperature": self.temperature,
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
            out = await self._complete(interaction_prompt(ix), interaction_grammar(ix.options), 40)
            option, _, line = out.partition("|")
            option = option.strip()
            if option not in ix.options:
                raise ValueError(f"bad option {option!r}")
            return option, clamp_words(line.strip()) or "..."
        except Exception as e:
            self._failed("interaction", e)
            return await self.fallback.choose_interaction(ix)

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
                 temperature: float = 0.7, concurrency: int = 4):
        super().__init__(url, fallback, timeout, temperature, concurrency)
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

    async def _chat(self, messages: list[dict], schema: dict, max_tokens: int) -> dict:
        async with self._sem:
            self.calls += 1
            r = await self._client.post(f"{self.url}/api/chat", json={
                "model": self.model,
                "messages": messages,
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": {"temperature": self.temperature, "num_predict": max_tokens},
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
            out = await self._chat(interaction_prompt(ix), schema, 48)
            if out["option"] not in ix.options:
                raise ValueError(f"bad option {out['option']!r}")
            return out["option"], clamp_words(str(out["line"]).strip()) or "..."
        except Exception as e:
            self._failed("interaction", e)
            return await self.fallback.choose_interaction(ix)

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
