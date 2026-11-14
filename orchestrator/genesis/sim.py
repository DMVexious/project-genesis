"""The core tick loop (PRD section 7).

1. State sync      - the engine's last response holds grid, stats and valid actions.
2. Prompt assembly - one NpcView per living NPC (stats + genome + valid actions).
3. Inference       - all NPCs decide concurrently (batched by llama-server).
4. Parsing         - grammar-constrained integer choices (+ interaction dialogue).
5. Execution       - integer choices go back to the engine, which resolves physics.
6. Memory logging  - fights, trades, talks and births are written to memory.
7. Broadcast       - a render frame (positions, emojis, dialogue) is returned.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .brain import Canned, Interaction, NpcView, base_option, clamp_words, describe_action
from .engine import GAME_MAP
from .engine.path import scan
from .engine.defs import (ACTIONS, ANIMALS, ATTRS, BUILDINGS, ELDER_AGE, EVENT_EMOJI, FX_EVENTS, GESTATION, INTERACTIONS,
                          ITEMS, MATURE_AGE, OBJECTS, SKILLS, TERRAIN, action_ticks, attr_of, building_glyphs,
                          items_text, option_text, same_action, speed_factor, years)
from .events import Director
from .memory import Memory
from .traits import (Genome, belief_text, make_name, make_village_name, random_genome, random_look, random_persona, splice,
                     splice_look, splice_persona, voice_text)

log = logging.getLogger(__name__)

BUILDING_GLYPHS = building_glyphs()
GROUND = {t.glyph: t.label for t in TERRAIN.values()}  # terrain glyph -> "grass", "shallow water"...
LOG_EMOJI = "💀🪦🏠🌀✨💞👶🎁🤝🫳⭐⚡❤🍖🎮🗣🏹🐺💦🔥🤒💪📖🌱☀🍂❄🌧⛈🌫🌨🥵🎉🧺🧳🧙☄🌑🌊🕯" + "".join(sorted({b.emoji for b in BUILDINGS}))  # stripped from news lines

# (chronicle lines for the living world: see Simulation._nature_event)
NATURE_EVENTS = frozenset({"season", "weather", "hunt", "mauled", "wolves", "ignite", "douse", "burn", "sick",
                           "recover", "teach"})
SEASON_LINES = {"spring": "🌱 Spring comes: the land wakes up", "summer": "☀️ Summer comes",
                "autumn": "🍂 Autumn comes: the leaves turn", "winter": "❄️ Winter comes: nothing will grow until spring"}
WEATHER_LINES = {"clear": "☀️ The skies clear", "rain": "🌧️ Rain sets in", "storm": "⛈️ A thunderstorm rolls in",
                 "fog": "🌫️ Fog rolls in", "snow": "🌨️ Snow begins to fall",
                 "drought": "🥵 A drought sets in: the land is parched"}
DEATHS = {"wolves": "🐺 {} was killed by wolves", "illness": "🤒 {} died of a fever", "fire": "🔥 {} died in a fire",
          "the cold": "❄️ {} froze to death"}


@dataclass
class NpcMeta:
    id: int
    name: str
    genome: Genome
    generation: int = 0
    parents: list[int] = field(default_factory=list)
    born_tick: int = 0
    mutated: list[str] = field(default_factory=list)
    pace: float = 1.0  # >1 = slower; every NPC moves and works at its own speed
    look: dict = field(default_factory=dict)  # inherited appearance (skin, hair, clothes...)
    persona: dict = field(default_factory=dict)  # strange belief + way of talking (traits.BELIEFS/VOICES)


PLAYER_WORDS = 30         # the most a player's line may run to
PLAYER_WAIT = 150         # ticks a player's conversation may take before it is dropped
ANSWER_SECONDS = 25.0     # how long a villager waits for the player to answer them
CHAT_IDLE = 110           # ticks (~25 s) of silence from the player before the other one wanders off
CHAT_REACH = 2            # how far apart the two may get before the conversation is over
CHAT_KEEP = 12            # lines of the conversation the other one keeps in mind (and the player sees)
WALK_LIMIT = 400          # how far (in steps) the player's "walk there" looks for a way
GOD_POWERS = ("heal", "feed", "smite")


@dataclass
class SimConfig:
    seed: int = 7
    width: int = 80
    height: int = 56
    population: int = 3       # founders; they start together and the village grows from them
    decay_every: int = 5      # ticks per unit of world time (hunger, energy, ageing, regrowth)
    plan_max_ticks: int = 12  # re-ask the brain at least this often while walking (steps)
    llm_interval: int = 18    # ticks between an NPC's LLM goal checks
    work_beat: int = 3        # a working NPC swings its tool (animation + sound) every this many ticks
    llm_budget: int = 2       # max brain requests in flight at once
    talk_budget: int = 2      # max conversations using the LLM at once (each makes up to 5 calls);
                              # with the LLM on, extra NPCs do something else instead of talking
    talk_cooldown: int = 110  # ticks before an NPC starts another conversation (~30 s); keeps it calm
    talk_gap: int = 9         # ticks between any two conversations starting, so people take turns
    courtship: int = 2        # affection (friendly exchanges, both ways) needed before "mate" is offered
    think_max_ticks: int = 40 # give up on a brain call after this many ticks (~6 s) and act on reflex
    convert_chance: float = 0.08  # chance a conversation spreads the speaker's belief to the listener
    llm_choices: int = 10     # the LLM is shown at most this many actions (the most appealing ones)
    map_name: str = GAME_MAP  # world preset (engine.MAPS)


class Simulation:
    def __init__(self, engine, brain, memory, config: SimConfig, talker=None):
        # Memory embedding/queries get one dedicated worker thread. Using the
        # shared default pool let a backlog of slow embeddings starve it, and
        # asyncio resolves host names on that pool, so LLM requests stalled too.
        self._mem_pool = ThreadPoolExecutor(1, thread_name_prefix="memory")
        self._recall_pool = ThreadPoolExecutor(1, thread_name_prefix="recall")  # never queued behind a flush
        self.engine, self.brain, self.memory, self.cfg = engine, brain, memory, config
        # LLM brains carry a heuristic fallback; that same brain handles reflexes.
        self.reflex = getattr(brain, "fallback", brain)
        # What the player reads (their conversations, the voice from the sky, villagers
        # speaking to them) can be written by a bigger model: see brain.make_talker.
        self.talker = talker or brain
        self.reset(config.seed)

    def reset(self, seed: int | None = None):
        if seed is not None:
            self.cfg.seed = seed
        self.rng = random.Random(self.cfg.seed)
        # personas draw from their own stream so they don't shift the world's dice
        self.persona_rng = random.Random(self.cfg.seed * 1_000_003 + 17)
        self.news: deque[str] = deque(maxlen=6)  # recent village events NPCs gossip about
        self.director = Director(self.cfg.seed)   # wildfires, migrants, festivals... (see events.py)
        self.mauled: dict[int, int] = {}          # NPC -> tick a wolf last bit them
        self.memory.reset()
        for task in getattr(self, "pending", {}).values():
            task.cancel()
        self.epoch = getattr(self, "epoch", 0) + 1  # which world this is: goes up with every new one
        self.state = self.engine.init(self.cfg.seed, self.cfg.width, self.cfg.height,
                                      self.cfg.population, self.cfg.decay_every, self.cfg.map_name)
        self.commands: list[dict] = []  # player/world commands waiting for the next tick
        self.meta: dict[int, NpcMeta] = {}
        for n in self.state["npcs"]:
            genome = random_genome(self.rng)
            if self.cfg.population <= 4 and genome.aggression == "Bloodthirsty":
                # a handful of founders with a killer among them is a very short story
                genome = dataclasses.replace(genome, aggression="Hostile")
            self._register(n["id"], genome, look=random_look(self.rng),
                           persona=random_persona(self.persona_rng))
        self.totals = {"births": 0, "deaths": 0, **{b.stat: 0 for b in BUILDINGS if b.stat},
                       "fights": 0, "trades": 0}
        self.homes: dict[int, tuple[int, int]] = {}  # NPC -> its home hut (built, inherited or claimed)
        self.vacant: set[tuple[int, int]] = set()    # huts whose owner died without an heir
        self.dialogue: list[dict] = []
        self.plans: dict[int, dict] = {}
        self.decision_stats = {"llm": 0, "reflex": 0, "plan": 0, "player": 0}
        self.last_llm: dict[int, int] = {}
        self.llm_neighbours: dict[int, frozenset] = {}  # who was adjacent at the last LLM decision
        self._last_seen: dict[int, tuple[int, frozenset]] = {}
        self.pending: dict[int, asyncio.Future] = {}   # brain calls in flight
        self.pending_since: dict[int, int] = {}        # tick each call was started
        self.pending_kind: dict[int, str] = {}         # "decide" (pure LLM call) or "interact"
        self.llm_talks = 0  # conversations in flight that are using the LLM
        self.last_talk: dict[int, int] = {}  # tick each NPC last started a conversation
        self.listening_to: dict[int, int] = {}  # speaker -> who they are talking to (latest conversation)
        self._flush_task: asyncio.Future | None = None
        self.llm_paused_until = 0  # backoff after the LLM missed a deadline
        self._llm_backoff = 0
        self.ready: dict[int, dict] = {}               # finished decisions waiting to be applied
        self.busy_until: dict[int, int] = {}           # tick each NPC may act again
        self.busy_from: dict[int, int] = {}            # tick its current action began (for the time bar)
        self.working: dict[int, dict] = {}             # timed work in progress: {act, start, end, source}
        self.last_any_talk = -10**9                    # tick the latest conversation started
        self.talk_wait: dict[int, int] = {}            # ticks an NPC has waited for its turn to speak
        self.roles: dict[int, str] = {}                # what each NPC is known for (its best skill)
        self.village_names: dict[int, str] = {}        # village id -> its name
        self.village_rng = random.Random(self.cfg.seed * 7_919 + 3)
        self.relations: dict[tuple[int, int], int] = {} # (village, other village) -> how its people feel (-/+)
        self.quiet: set[str] = set()                   # chronicle lines too small to be village news
        self._stats: dict[int, dict] = {}              # the living, by id, as of this tick
        # the player: who they have become (if anyone), what they told that body to do next,
        # and what they should be told (a refusal, an unreachable spot...)
        self.possessed: int | None = None
        self.order: dict | None = None
        self.note: tuple[int, str] | None = None
        # someone speaking to the player's body, waiting for the player to answer:
        # {"from": id, "said": str, "option": str, "consent": bool, "reply": Future}
        self.incoming: dict | None = None
        # a conversation between the player's body and someone else, until one of them leaves:
        # {"with": id, "lines": [(speaker id, text)], "last": tick, "ask": bool, "task": Future | None}
        self.chat: dict | None = None
        self.chat_out: list[dict] = []                  # chat lines to show in the next frame
        self.voice_asks: list[tuple[int, str]] = []     # what the voice from the sky said, to whom
        self.voices: list[asyncio.Future] = []          # answers to it, being written
        self.voice_lines: list[dict] = []               # ...and those ready to show
        self.god_log: list[str] = []                    # what the gods did since the last tick
        self.work_fx: list[dict] = []                # this tick's tool swings of NPCs at work
        self.activity: dict[int, str] = {}             # what each NPC is doing right now
        self.intent: dict[int, str] = {}
        self.source: dict[int, str] = {}
        self.started: dict[int, int] = {}              # NPCs that began an action this tick -> duration
        self.last_frame = self._frame({}, [])

    def _register(self, nid: int, genome: Genome, **kw) -> NpcMeta:
        name = make_name(self.rng, {m.name for m in self.meta.values()})
        self.meta[nid] = NpcMeta(nid, name, genome, pace=round(self.rng.uniform(0.8, 1.5), 2), **kw)
        return self.meta[nid]

    def names(self) -> dict[int, str]:
        return {i: m.name for i, m in self.meta.items()}

    def view(self, n: dict, actions: bool = True) -> NpcView:
        """What a brain may know about an NPC. ``actions=False`` leaves the action
        list empty, for NPCs that are busy and not about to choose anything."""
        m = self.meta[n["id"]]
        x0, y0 = n["x"], n["y"]
        nearby = {}
        if actions:  # (what stands around only matters to someone choosing what to do)
            rows = [row[max(0, x0 - 5):x0 + 6] for row in self.state["tiles"][max(0, y0 - 5):y0 + 6]]
            count = lambda chars: sum(r.count(c) for r in rows for c in chars)  # noqa: E731
            nearby = {key: count(glyphs) for key, glyphs in BUILDING_GLYPHS.items()}
        home = self.homes.get(n["id"])
        dist = None if home is None else abs(home[0] - x0) + abs(home[1] - y0)
        ground = GROUND.get(self.state["terrain"][y0][x0], "")
        village = self._village_info(n.get("village", -1))
        if village:  # (how far from home they are: lighting a fire far away founds a new village)
            village["dist"] = max(abs(village["x"] - x0), abs(village["y"] - y0))
        c = self.state.get("clock", {})
        world = {k: c[k] for k in ("season", "time", "weather", "night") if k in c}
        return NpcView(n["id"], m.name, m.genome, n, self.state["actions"].get(n["id"], []) if actions else [],
                       nearby.get("hut", 0), nearby, dist, n["age"] >= ELDER_AGE, m.persona, ground, village,
                       world)

    # --- villages -------------------------------------------------------------------
    def village_name(self, vid: int) -> str:
        if vid not in self.village_names:
            self.village_names[vid] = make_village_name(self.village_rng, set(self.village_names.values()))
        return self.village_names[vid]

    def _village_info(self, vid: int) -> dict:
        v = next((v for v in self.state.get("villages", []) if v["id"] == vid), None)
        if v is None:
            return {}
        sites = [s[2] for s in self.state.get("sites", [])
                 if max(abs(s[0] - v["x"]), abs(s[1] - v["y"])) <= v["radius"]]
        return {"id": vid, "name": self.village_name(vid), "people": v["people"], "room": v["room"],
                "store": v["stock"], "has": v["buildings"], "sites": sites, "x": v["x"], "y": v["y"],
                "radius": v["radius"]}

    def _update_relations(self):
        """How each village's people feel about each other village's, from what they remember."""
        people: dict[int, list[int]] = {}
        for n in self.alive():
            if n.get("village", -1) != -1:
                people.setdefault(n["village"], []).append(n["id"])
        self.relations = {}
        for a, mine in people.items():
            for b, theirs in people.items():
                if a != b:
                    total = sum(self.memory.grudge(i, j) for i in mine for j in theirs)
                    self.relations[(a, b)] = round(total / max(1, len(mine)))

    # --- outside intervention -----------------------------------------------------
    def enqueue(self, cmd: dict):
        """Queue a world command (see ``World.apply``); it takes effect at the start of the next tick."""
        self.commands.append(cmd)

    def _drain_commands(self):
        if not self.commands:
            return
        cmds, self.commands = self.commands, []
        self.state = self.engine.apply(cmds)
        for e in self.state["events"]:
            if e["type"] == "arrive" and e["npc"] not in self.meta:  # a newcomer needs a name and a nature
                self._register(e["npc"], random_genome(self.rng), look=random_look(self.rng),
                               persona=dict(e.get("persona") or random_persona(self.persona_rng)),
                               born_tick=self.state["tick"])

    def alive(self) -> list[dict]:
        return [n for n in self.state["npcs"] if n["alive"]]

    # --- one tick ------------------------------------------------------------
    # NPCs are asynchronous: every action lasts a random number of ticks scaled
    # by the NPC's own pace, so they act out of step with each other. Brain
    # calls run as background tasks: the world never waits for inference. An
    # NPC that is "thinking" just pauses until its answer lands.

    async def tick(self) -> dict:
        await asyncio.sleep(0)  # let finished brain tasks deliver their results
        self._drain_commands()
        names = self.names()
        living = self._stats = {n["id"]: n for n in self.alive()}
        now = self.state["tick"] + 1  # the tick about to be executed
        # (only NPCs free to act this tick need their action list: working it out costs a path search)
        views = {nid: self.view(n, self.busy_until.get(nid, 0) <= now) for nid, n in living.items()}
        self._harvest(living)
        # Someone being spoken to waits for the speaker to finish (the LLM takes a few
        # seconds to write the lines). Without this, partners wandered off mid-proposal:
        # 10 of 14 accepted proposals in one run never reached the engine.
        listeners = {t for s, t in self.listening_to.items()
                     if (s in self.pending and self.pending_kind.get(s) == "interact")
                     or self.ready.get(s, {}).get("type") == "interact"}

        choices: dict[int, tuple[int, int]] = {}
        sources: dict[int, str] = {}
        lines: dict[int, dict] = {}
        candidates: list[tuple[tuple, NpcView]] = []
        finishing: set[int] = set()  # NPCs whose timed work is done: it takes effect this tick
        for nid in [i for i in self.working if i not in living]:
            del self.working[nid]
        if self.possessed is not None and self.possessed not in living:  # the body died under the player
            self._note(now, "You died.")
            self.release()
        for nid, v in views.items():
            if self.busy_until.get(nid, 0) > now:
                continue
            work = self.working.pop(nid, None)
            if work is not None:
                idx = next((i for i, a in enumerate(v.actions) if same_action(a, work["act"])), None)
                if idx is not None:  # (None: the tree was felled, the wood spent... the effort is lost)
                    choices[nid], sources[nid] = (idx, 0), work["source"]
                    finishing.add(nid)
                    continue
            if nid == self.possessed:
                self._player_turn(v, now, choices, sources, lines)  # the player decides, never a brain
                continue
            if nid in self.pending:
                if not self._give_up_thinking(v, now):
                    continue
                self._reflex(v, choices, sources)  # too slow or in danger: act now
                continue
            if nid in listeners and v.stats["hunger"] >= 20 and v.stats["energy"] >= 10:
                continue  # listening: stay put (the speaker gives up after think_max_ticks anyway)
            ready = self.ready.pop(nid, None)
            if ready is not None and self._apply_ready(v, ready, choices, sources, lines):
                continue
            idx = self._continue_plan(v)
            if idx is not None:
                choices[nid], sources[nid] = (idx, 0), "plan"
                continue
            prio = self._llm_priority(v, now)
            if prio is not None:
                candidates.append((prio, v))
            else:
                self._reflex(v, choices, sources)

        # LLM slots: at most `llm_budget` requests in flight at once. Social
        # moments first, then the most overdue goal checks; the rest act on
        # reflex this time and stay first in line.
        candidates.sort(key=lambda c: c[0])
        free = max(0, self.cfg.llm_budget - len(self.pending))
        for i, (_, v) in enumerate(candidates):
            if i < free:
                self._spawn(v.id, self._decide(v, names), "decide")
                self.last_llm[v.id] = now
                self.llm_neighbours[v.id] = self._neighbours(v)
            else:
                self._reflex(v, choices, sources)

        # Interactions need dialogue first: hand them to a background task and
        # execute when the line is ready (unless already resolved above).
        for nid, (idx, _) in list(choices.items()):
            act = views[nid].actions[idx]
            if act["kind"] == "interact" and nid not in lines:
                llm_on = self.reflex is not self.brain
                # People take turns: an NPC rests between the conversations it starts, and
                # two conversations never begin at the same moment. (Starting a family is
                # rare enough to be worth interrupting for.)
                cooling = (now - self.last_talk.get(nid, -10**9) < self.cfg.talk_cooldown
                           and "mate" not in act["options"] and sources.get(nid) != "llm")
                cooling = cooling or (self.chat is not None and act["target"] in (self.chat["with"], self.possessed))
                crowded = now - self.last_any_talk < self.cfg.talk_gap
                # Every line should be the LLM's: with no free LLM slot the NPC does something
                # else instead of saying a canned line. (If the LLM is down altogether, canned
                # lines are the fallback.)
                busy = llm_on and not self.llm_paused() and self.llm_talks >= self.cfg.talk_budget
                if (crowded or busy) and sources.get(nid) == "llm" and self.talk_wait.get(nid, 0) < 12:
                    # The LLM decided to speak: wait a moment for a turn rather than drop it.
                    self.talk_wait[nid] = self.talk_wait.get(nid, 0) + 1
                    self.ready[nid] = {"type": "act", "action": act, "source": "llm"}
                    del choices[nid], sources[nid]
                    continue
                if cooling or crowded or busy:
                    self.talk_wait.pop(nid, None)
                    avoid = frozenset({"interact", "goto_npc"} | ({"build"} if llm_on else set()))
                    choices[nid] = (self.reflex.pick_action(views[nid], avoid), 0)
                    sources[nid] = "reflex"
                    self._commit_plan(views[nid], views[nid].actions[choices[nid][0]])
                    continue
                del choices[nid]
                self.talk_wait.pop(nid, None)
                self.last_talk[nid] = self.last_any_talk = now
                use_llm = llm_on
                self._spawn(nid, self._interaction(views, nid, act, sources.pop(nid, "reflex"), use_llm), "interact")
                self.listening_to[nid] = act["target"]
                if use_llm:  # released when the task ends, even if it is cancelled before it starts
                    self.llm_talks += 1
                    self.pending[nid].add_done_callback(self._talk_done)

        self.started = {}
        for nid in list(choices):
            if nid in finishing:
                continue  # decided (and counted) when the work began
            v = views[nid]
            act = v.actions[choices[nid][0]]
            self.decision_stats[sources[nid]] += 1
            self.intent[nid] = describe_action(act, names, living[nid])
            self.source[nid] = sources[nid]
            self._last_seen[nid] = (living[nid]["health"], self._neighbours(v))
            if ACTIONS[act["kind"]].timed:
                # Work takes time: the NPC is busy until it is done, and only then does the
                # engine carry it out (see `finishing` above).
                del choices[nid]
                dur = self._duration(nid, act)
                self.working[nid] = {"act": act, "start": now, "end": now + dur, "source": sources[nid]}
                self._occupy(nid, act["kind"], now, dur)

        # 5. Execution.
        self.state = self.engine.step(choices)
        for nid, (idx, opt) in choices.items():
            act = views[nid].actions[idx]
            if nid in finishing:
                self.started[nid] = 1
                continue
            if act["kind"] == "interact":
                # both sides are taken up by it: the other one stays and listens
                option = act["options"][opt] if 0 <= opt < len(act["options"]) else "talk"
                option = base_option(option)
                dur = max(1, round(INTERACTIONS[option].ticks * self.meta[nid].pace))
                self._occupy(nid, option, now, dur)
                other = act["target"]
                if other in living and other not in self.working:
                    self._occupy(other, option if option == "mate" else "listen", now, dur, started=False)
                    self.plans.pop(other, None)
                continue
            self._occupy(nid, act["kind"], now, self._duration(nid, act))
        beat = self.cfg.work_beat
        self.work_fx = [{"type": "work", "npc": nid, "kind": w["act"]["kind"], "what": w["act"].get("what", ""),
                         "x": w["act"].get("x"), "y": w["act"].get("y")}
                        for nid, w in self.working.items()
                        if w["end"] - now > 1 and (now - w["start"]) % beat == 0]

        # 6. Memory logging + births.
        log_lines = self.god_log + self._process_events(self.state["events"], lines, now)
        log_lines += self.director.tick(self, now)
        self.god_log = []
        self._collect_voices(now)
        self._run_chat(now, living)
        for line in log_lines:
            if " talks to " not in line and line not in self.quiet:
                self.news.append(line.lstrip(LOG_EMOJI + "️ ").strip())
        if now % 50 == 0:
            self._update_relations()
        if now % 10 == 0:  # embedding is costly; batch it (recall() flushes first anyway)
            self._flush_memory()
        return self._frame(lines, log_lines)

    # --- the player ------------------------------------------------------------------
    # The player may become any villager: that body then does only what the
    # player says (walk somewhere, chop, eat, talk, hit...), at the same pace and
    # by the same rules as everyone else. Or, as a god, speak to anyone from the
    # sky, and heal, feed or strike them down.

    def possess(self, nid: int) -> bool:
        if not any(n["id"] == nid and n["alive"] for n in self.state["npcs"]):
            return False
        if self.possessed is not None:
            self.release()
        self.possessed, self.order = nid, None
        task = self.pending.pop(nid, None)  # whatever it was thinking about, it is not its own mind now
        if task is not None:
            task.cancel()
        self.pending_since.pop(nid, None)
        self.ready.pop(nid, None)
        self.plans.pop(nid, None)
        self.enqueue({"op": "control", "npc": nid, "on": True})
        self.god_log.append(f"🎮 You become {self.meta[nid].name}")
        return True

    def release(self):
        if self.possessed is None:
            return
        self.end_chat(quiet=True)
        self.enqueue({"op": "control", "npc": self.possessed, "on": False})
        if self.incoming is not None and not self.incoming["reply"].done():
            self.incoming["reply"].set_result({"text": "", "accept": False})
        if self.possessed in self.meta:
            self.god_log.append(f"🎮 You leave {self.meta[self.possessed].name}'s body")
        self.pending.pop(self.possessed, None)
        self.possessed = self.order = None

    def command(self, order: dict) -> bool:
        """What the player tells their body to do next (it replaces any earlier order):
        {"kind": "act", "action": {...}}       one of the actions on offer
        {"kind": "step", "dx": 1, "dy": 0}      one step
        {"kind": "walk", "x": 10, "y": 4}       walk there (and, if something stands there, use it)
        {"kind": "interact", "target": id, "option": "talk" | "fight" | "trade:wood:food"..., "text": "..."}
        """
        if self.possessed is None or not isinstance(order, dict):
            return False
        kind = order.get("kind")
        if kind == "act" and isinstance(order.get("action"), dict) and order["action"].get("kind") in ACTIONS:
            self.order = {"kind": "act", "action": order["action"]}
        elif kind == "step" and (order.get("dx"), order.get("dy")) in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            self.order = {"kind": "step", "dx": order["dx"], "dy": order["dy"]}
        elif kind == "walk" and isinstance(order.get("x"), int) and isinstance(order.get("y"), int):
            self.order = {"kind": "walk", "x": order["x"], "y": order["y"]}
        elif kind == "interact" and order.get("target") in self.meta and order.get("target") != self.possessed:
            self.order = {"kind": "interact", "target": order["target"], "option": str(order.get("option", "talk")),
                          "text": clamp_words(str(order.get("text", "")).strip(), PLAYER_WORDS)}
        else:
            return False
        return True

    def _note(self, tick: int, text: str):
        self.note = (tick, text)

    def _cells_around(self, x: int, y: int, reach: int) -> list[int]:
        """Walkable tiles within ``reach`` of (x, y) (all eight ways), as tile indices."""
        w = self.engine.world
        return [w.idx(xx, yy) for yy in range(y - reach, y + reach + 1) for xx in range(x - reach, x + reach + 1)
                if (xx, yy) != (x, y) and w.passable(xx, yy)]

    def _walk_toward(self, v: NpcView, cells: list[int], choices, sources) -> bool | None:
        """Take the first step of the way to the nearest of ``cells``. Returns True
        once there, False if a step was taken, None if there is no way."""
        w = self.engine.world
        if w.idx(v.stats["x"], v.stats["y"]) in cells:
            return True
        hit = scan(w, v.stats["x"], v.stats["y"], (), [], WALK_LIMIT, cells).get("cell")
        if hit is None:
            return None
        idx = next((i for i, a in enumerate(v.actions) if a["kind"] == "step" and (a["dx"], a["dy"]) == hit[1:3]), None)
        if idx is None:
            return None
        choices[v.id], sources[v.id] = (idx, 0), "player"
        return False

    def _player_turn(self, v: NpcView, now: int, choices, sources, lines):
        nid = v.id
        if nid in self.pending:  # waiting for the other one to answer
            if now - self.pending_since.get(nid, now) > PLAYER_WAIT:
                self.pending.pop(nid).cancel()
                self._note(now, "They never answered.")
            return
        ready = self.ready.pop(nid, None)
        if ready is not None:
            if not self._apply_ready(v, ready, choices, sources, lines):
                self._note(now, "They walked off before you could.")
            return
        order = self.order
        if order is None:
            return  # idle: the body stands and waits for the player
        kind = order["kind"]
        if kind == "act":
            self.order = None
            idx = next((i for i, a in enumerate(v.actions) if same_action(a, order["action"])), None)
            if idx is None:
                self._note(now, "You can't do that right now.")
                return
            choices[nid], sources[nid] = (idx, 0), "player"
            self._commit_plan(v, v.actions[idx])
            self.plans.pop(nid, None)
        elif kind == "step":
            self.order = None
            idx = next((i for i, a in enumerate(v.actions)
                        if a["kind"] == "step" and (a["dx"], a["dy"]) == (order["dx"], order["dy"])), None)
            if idx is not None:
                choices[nid], sources[nid] = (idx, 0), "player"
        elif kind == "walk":
            w = self.engine.world
            x, y = order["x"], order["y"]
            if not w.inb(x, y):
                self.order = None
                return
            # a walkable spot: go there; something standing there: go next to it, then use it
            target = [w.idx(x, y)] if w.passable(x, y) else self._cells_around(x, y, 1)
            got = self._walk_toward(v, target, choices, sources)
            if got is None:
                self.order = None
                self._note(now, "You can't get there.")
            elif got:
                self.order = None
                use = next((i for i, a in enumerate(v.actions)
                            if a.get("x") == x and a.get("y") == y and a["kind"] in ("chop", "forage")), None)
                if use is not None:  # (a tree: chop it; a bush: pick it)
                    choices[nid], sources[nid] = (use, 0), "player"
        elif kind == "interact":
            target = next((n for n in self.state["npcs"] if n["id"] == order["target"] and n["alive"]), None)
            if target is None:
                self.order = None
                self._note(now, "They're gone.")
                return
            act = next((a for a in v.actions if a["kind"] == "interact" and a["target"] == target["id"]), None)
            if act is None:  # not close enough yet: walk over
                if self._walk_toward(v, self._cells_around(target["x"], target["y"], 1), choices, sources) is None:
                    self.order = None
                    self._note(now, f"You can't reach {self.meta[target['id']].name}.")
                return
            self.order = None
            option = order["option"]
            if option not in act["options"]:  # "trade" alone means the first trade on offer
                option = next((o for o in act["options"] if base_option(o) == option), None)
            if option is None:
                self._note(now, f"You can't {order['option']} with {self.meta[target['id']].name} right now.")
                return
            if base_option(option) == "talk" and order["text"]:  # words: that is a conversation
                if self.chat is None or self.chat["with"] != target["id"]:
                    self._open_chat(target["id"], [], now)
                self.chat_say(order["text"])
                return
            self._spawn(nid, self._player_interaction(v, act, option, order["text"]), "interact")
            self.listening_to[nid] = target["id"]
            self.last_talk[nid] = now

    async def _player_interaction(self, me: NpcView, act: dict, option: str, text: str) -> dict:
        """The player, in someone's body, says or does something to another villager,
        who always answers (written by the LLM when there is one)."""
        target = next(n for n in self.state["npcs"] if n["id"] == act["target"])
        other = self.view(target, actions=False)
        their = await self._recall(other.id, me.id)
        rev = Interaction(other, me, [option], [m.text for m in their], self.memory.grudge(other.id, me.id),
                          list(self.news))
        context, key = option, base_option(option)
        heard = text or {"fight": "(says nothing, just comes at you)"}.get(key, "(says nothing)")
        if INTERACTIONS[key].consent:
            judge = self.reflex if key == "mate" else self.brain
            if not await judge.consent(rev, option, heard):
                what = {"mate": "start a family"}.get(key, key)
                self.memory.log(Memory(me.id, other.id, self.state["tick"], "refused",
                                       f"{other.name} refused to {what} with me.", -1))
                self._note(self.state["tick"], f"{other.name} said no.")
                context, option = f"refused_{key}", "talk"
        replier = self.talker if hasattr(self.talker, "reply") else self.reflex
        reply = await replier.reply(rev, heard, context)
        return {"type": "interact", "action": act, "option": option, "source": "player",
                "line": {"npc": me.id, "target": other.id, "text": text, "reply": clamp_words(reply),
                         "llm": False, "reply_llm": not isinstance(reply, Canned), "player": True}}

    async def _player_answers(self, act, me: NpcView, player: NpcView, option: str, said: str, said_llm: bool,
                              source: str) -> dict:
        """A villager speaks to the body the player is in: show the player what they
        said (and what they want), and wait for the player's answer."""
        key = base_option(option)
        fut = asyncio.get_running_loop().create_future()
        self.incoming = {"from": me.id, "said": said, "option": option, "consent": INTERACTIONS[key].consent,
                         "want": option_text(option, mine=False) if key != "talk" else "", "reply": fut}
        try:
            answer = await asyncio.wait_for(fut, ANSWER_SECONDS)
        except asyncio.TimeoutError:
            answer = {"text": "", "accept": False}
        finally:
            if self.incoming is not None and self.incoming["reply"] is fut:
                self.incoming = None
        if INTERACTIONS[key].consent and not answer.get("accept"):
            what = {"mate": "start a family"}.get(key, key)
            self.memory.log(Memory(me.id, player.id, self.state["tick"], "refused",
                                   f"{player.name} refused to {what} with me.", -1))
            option = "talk"
        if key == "talk" and answer.get("text") and self.possessed == player.id:
            # they spoke, you answered: now it's a conversation, and they answer you back
            self._open_chat(me.id, [(me.id, said)] if said else [], self.state["tick"], said_llm)
            self.chat_say(answer["text"])
            return {"type": "none", "source": source}
        return {"type": "interact", "action": act, "option": option, "source": source,
                "line": {"npc": me.id, "target": player.id, "text": said, "reply": answer.get("text", ""),
                         "llm": said_llm, "reply_llm": False, "player_reply": True}}

    # --- conversations with the player -----------------------------------------------
    # Once the player speaks to someone, the two are locked on: the other one stops
    # what they are doing and answers every line, with the whole conversation in
    # mind, until one of them leaves. They may leave when they like (bored, upset,
    # hungry...); the player leaves by walking off or ending it. Everyone else
    # carries on with their lives meanwhile.

    def _open_chat(self, partner: int, lines: list[tuple[int, str]], tick: int, first_llm: bool = False):
        if self.chat is not None:
            self.end_chat(quiet=True)
        self.chat = {"with": partner, "lines": [], "last": tick, "ask": False, "task": None}
        self.plans.pop(partner, None)
        task = self.pending.get(partner)
        if task is not None and task is not asyncio.current_task():  # (whatever it was thinking about can wait)
            self.pending.pop(partner).cancel()
            self.pending_since.pop(partner, None)
        for who, text in lines:
            self._chat_line(who, text, tick, llm=first_llm)

    def _chat_line(self, who: int, text: str, tick: int, llm: bool = False):
        """Someone in the conversation says something: it is shown, and both remember it."""
        c = self.chat
        other = c["with"] if who == self.possessed else self.possessed
        c["lines"].append((who, text))
        del c["lines"][:-CHAT_KEEP]
        c["last"] = tick
        nm = self.meta[who].name
        self._remember(who, other, tick, "talk", f'I told {self.meta[other].name}: "{text}"', 1)
        self._remember(other, who, tick, "talk", f'{nm} told me: "{text}"', 1)
        self.chat_out.append({"npc": who, "target": other, "text": text, "tick": tick, "kind": "chat",
                              "llm": llm, "player": who == self.possessed})

    def chat_say(self, text: str) -> bool:
        """The player says something in the conversation; the other one will answer."""
        c = self.chat
        text = clamp_words(str(text).strip(), PLAYER_WORDS)
        if c is None or not text or self.possessed is None:
            return False
        if c["task"] is not None and not c["task"].done():
            self._note(self.state["tick"], f"{self.meta[c['with']].name} is still answering.")
            return False
        self._chat_line(self.possessed, text, self.state["tick"])
        c["ask"] = True
        self.enqueue({"op": "train", "npc": self.possessed, "skill": "speech", "xp": 2})
        return True

    def end_chat(self, quiet: bool = False, note: str = ""):
        c, self.chat = self.chat, None
        if c is None:
            return
        if c["task"] is not None:
            c["task"].cancel()
        self.busy_until.pop(c["with"], None)  # free to go about their life again
        if note:
            self._note(self.state["tick"], note)
        elif not quiet and c["with"] in self.meta:
            self._note(self.state["tick"], f"You stop talking to {self.meta[c['with']].name}.")

    async def _chat_reply(self, partner: dict, player: dict, lines: list[tuple[int, str]]) -> tuple[str, bool, bool]:
        me, other = self.view(partner, actions=False), self.view(player, actions=False)
        recalled = await self._recall(me.id, other.id)
        ix = Interaction(me, other, ["talk"], [m.text for m in recalled], self.memory.grudge(me.id, other.id),
                         list(self.news))
        history = [(self.meta[who].name, text) for who, text in lines]
        brain = self.talker if hasattr(self.talker, "chat") else self.reflex
        line, leave = await brain.chat(ix, history)
        return clamp_words(line), leave, not isinstance(line, Canned)

    def _run_chat(self, now: int, living: dict):
        c = self.chat
        if c is None:
            return
        pid, oid = self.possessed, c["with"]
        if pid is None or pid not in living or oid not in living:
            self.end_chat(quiet=True)
            return
        me, them = living[pid], living[oid]
        name = self.meta[oid].name
        if max(abs(me["x"] - them["x"]), abs(me["y"] - them["y"])) > CHAT_REACH:
            self._remember(oid, pid, now, "talk", f"{self.meta[pid].name} just walked off while we were talking.", -1)
            self.end_chat(note=f"You walked away from {name}.")
            return
        task = c["task"]
        if task is not None and task.done():
            c["task"] = None
            if not task.cancelled() and task.exception() is None:
                line, leave, llm = task.result()
                if line:
                    self._chat_line(oid, line, now, llm=llm)
                if leave:
                    self.end_chat(note=f"{name} ends the conversation.")
                    return
        elif task is None and c["ask"]:
            c["ask"] = False
            c["task"] = asyncio.ensure_future(self._chat_reply(them, me, list(c["lines"])))
        elif task is None and now - c["last"] > CHAT_IDLE:
            self._chat_line(oid, self.reflex.rng.choice(["Okay... I'll be going then.", "Hello? Fine, I'm off.",
                                                         "You've gone quiet. See you."]), now)
            self.end_chat(note=f"{name} got bored of waiting and left.")
            return
        # locked on: they stand and talk (everyone else carries on)
        self.busy_until[oid] = now + 2
        self.busy_from[oid] = now
        self.activity[oid] = "chat"

    def answer(self, text: str, accept: bool = False) -> bool:
        """The player's answer to whoever is speaking to their body."""
        if self.incoming is None or self.incoming["reply"].done():
            return False
        self.incoming["reply"].set_result({"text": clamp_words(str(text).strip(), PLAYER_WORDS),
                                           "accept": bool(accept)})
        return True

    def say(self, nid: int, text: str) -> bool:
        """The player, as a voice from the sky, speaks to a villager, who answers."""
        text = clamp_words(str(text).strip(), PLAYER_WORDS)
        n = next((n for n in self.state["npcs"] if n["id"] == nid and n["alive"]), None)
        if n is None or not text or self.possessed is not None:  # (in a body, a god's voice is gone)
            return False
        self.voice_asks.append((nid, text))  # (answered from the tick loop: see _collect_voices)
        return True

    async def _answer_voice(self, n: dict, text: str) -> dict:
        v = self.view(n, actions=False)
        before = [m.text for m in await self._recall(n["id"], -1)]
        line = await self.talker.answer_sky(v, text, before, list(self.news)) if hasattr(self.talker, "answer_sky") \
            else "Who said that?!"
        return {"npc": n["id"], "said": text, "reply": clamp_words(line), "llm": not isinstance(line, Canned)}

    def _collect_voices(self, tick: int):
        for nid, text in self.voice_asks:
            n = self._stats.get(nid)
            if n is not None:
                self.voices.append(asyncio.ensure_future(self._answer_voice(n, text)))
        self.voice_asks = []
        for task in [t for t in self.voices if t.done()]:
            self.voices.remove(task)
            if task.cancelled() or task.exception() is not None:
                continue
            r = task.result()
            if r["npc"] not in self._stats:
                continue  # died before they could answer
            self._remember(r["npc"], -1, tick, "voice", f'A voice from the sky told me: "{r["said"]}"', 0)
            self._remember(r["npc"], -1, tick, "voice", f'I answered the voice: "{r["reply"]}"', 0)
            self.voice_lines.append(r)

    def god(self, power: str, nid: int) -> bool:
        """Heal, feed or strike down a villager."""
        n = next((n for n in self.state["npcs"] if n["id"] == nid and n["alive"]), None)
        if n is None or power not in GOD_POWERS or self.possessed is not None:  # (no god powers while in a body)
            return False
        name = self.meta[nid].name
        if power == "heal":
            for stat in ("health", "energy"):
                self.enqueue({"op": "stat", "npc": nid, "stat": stat, "value": 100})
            self.god_log.append(f"❤️ The gods heal {name}")
        elif power == "feed":
            self.enqueue({"op": "stat", "npc": nid, "stat": "hunger", "value": 100})
            self.enqueue({"op": "give", "npc": nid, "item": "food", "n": 3})
            self.god_log.append(f"🍖 The gods feed {name}")
        else:
            self.enqueue({"op": "kill", "npc": nid, "cause": "lightning"})
        return True

    def _player_frame(self) -> dict | None:
        """What the player's body can do right now, for the controls."""
        pid = self.possessed
        me = next((n for n in self.state["npcs"] if n["id"] == pid and n["alive"]), None) if pid is not None else None
        if me is None:
            return None
        names = self.names()
        acts = []
        for i, a in enumerate(self.state["actions"].get(pid, [])):
            if a["kind"] in ("step", "goto", "goto_npc", "wander"):
                continue  # (walking is done by clicking the ground or with the keys)
            entry = {"i": i, "kind": a["kind"], "label": describe_action(a, names, me), "action": a}
            if a["kind"] == "interact":
                entry["choices"] = [{"option": o, "label": option_text(o)} for o in a["options"]]
            acts.append(entry)
        tick = self.state["tick"]
        inc = self.incoming
        heard = None if inc is None else {"from": inc["from"], "name": names.get(inc["from"], "?"), "said": inc["said"],
                                          "want": inc["want"], "consent": inc["consent"]}
        c = self.chat
        chat = None if c is None else {
            "with": c["with"], "name": self.meta[c["with"]].name,
            "lines": [{"me": who == pid, "who": "You" if who == pid else self.meta[who].name, "text": text}
                      for who, text in c["lines"]],
            "waiting": c["ask"] or (c["task"] is not None and not c["task"].done())}
        return {"id": pid, "name": self.meta[pid].name, "actions": acts, "incoming": heard, "chat": chat,
                "order": self.order["kind"] if self.order else "",
                "note": self.note[1] if self.note and tick - self.note[0] < 25 else ""}

    def _occupy(self, nid: int, doing: str, now: int, dur: int, started: bool = True):
        """Mark an NPC as busy with something for ``dur`` ticks."""
        self.busy_from[nid] = now
        self.busy_until[nid] = max(now + dur, self.busy_until.get(nid, 0) if not started else 0)
        self.activity[nid] = doing
        if started:
            self.started[nid] = dur

    def _flush_memory(self):
        # Embedding a batch can take a few hundred ms; do it off the event loop
        # so the tick loop and WebSocket broadcasts don't stall.
        if self._flush_task is None or self._flush_task.done():
            loop = asyncio.get_running_loop()
            self._flush_task = loop.run_in_executor(self._mem_pool, self.memory.flush)

    async def settle(self):
        """Wait for every brain call in flight (used by the headless runner,
        whose ticks are otherwise far faster than inference)."""
        if self.pending:
            await asyncio.gather(*self.pending.values(), return_exceptions=True)

    def _give_up_thinking(self, v: NpcView, now: int) -> bool:
        """A thinking NPC stands still while hunger and energy keep falling. If
        the answer takes too long, or the NPC is about to starve or collapse,
        drop the call and let reflexes take over."""
        s = v.stats
        waited = now - self.pending_since.get(v.id, now)
        in_danger = s["hunger"] < 20 or s["energy"] < 10
        # (the danger rule waits a few ticks so a normal-speed answer, such as
        # the dialogue for an interaction a reflex just chose, can still land)
        if waited < self.cfg.think_max_ticks and not (in_danger and waited >= 5):
            return False
        if self.incoming is not None and self.incoming["from"] == v.id and not in_danger:
            return False  # waiting for the player to answer (they get ANSWER_SECONDS)
        self.pending.pop(v.id).cancel()
        self.pending_since.pop(v.id, None)
        self.last_llm[v.id] = now
        self.llm_neighbours[v.id] = self._neighbours(v)
        # Only a late pure LLM decision says the LLM is slow (an interaction can also
        # wait on memory recall, which is not the LLM's fault).
        if (waited >= self.cfg.think_max_ticks and self.reflex is not self.brain
                and self.pending_kind.get(v.id) == "decide"):
            # The backend is stalled (cold load, overloaded GPU...). Stop routing
            # decisions to it for a while, doubling the pause on repeat misses.
            self._llm_backoff = min(400, max(self.cfg.think_max_ticks, self._llm_backoff * 2))
            if self.llm_paused_until <= now:
                log.warning("LLM missed the %d-tick deadline; reflexes only for %d ticks",
                            self.cfg.think_max_ticks, self._llm_backoff)
            self.llm_paused_until = now + self._llm_backoff
        return True

    def llm_paused(self, tick: int | None = None) -> bool:
        return (self.state["tick"] if tick is None else tick) < self.llm_paused_until

    def _duration(self, nid: int, act: dict) -> int:
        """How many ticks an action takes this NPC: its base length, their own
        pace, how practised and how able they are, and a little chance."""
        skill = speed_factor(self._stats.get(nid, {}), act)
        return max(1, round(action_ticks(act) * self.meta[nid].pace * skill * self.rng.uniform(0.75, 1.35)))

    def _reflex(self, v: NpcView, choices, sources):
        # What to build is a deliberate choice: while an LLM is answering, only it
        # decides to build. Reflexes build only in heuristic-only runs or while
        # the LLM is paused for being too slow.
        if not v.actions:  # (nothing they can do this tick: never let one villager stop the world)
            log.warning("npc %d has no actions at tick %d", v.id, self.state["tick"])
            return
        llm_on = self.reflex is not self.brain and not self.llm_paused()
        idx = self.reflex.pick_action(v, frozenset({"build"}) if llm_on else frozenset())
        choices[v.id], sources[v.id] = (idx, 0), "reflex"
        self._commit_plan(v, v.actions[idx])

    def _spawn(self, nid: int, coro, kind: str):
        self.pending[nid] = asyncio.ensure_future(coro)
        self.pending_kind[nid] = kind
        self.pending_since[nid] = self.state["tick"]
        self.activity[nid] = "thinking"

    def _harvest(self, living):
        for nid, task in list(self.pending.items()):
            if nid not in living:  # died while thinking
                task.cancel()
                del self.pending[nid]
                self.pending_since.pop(nid, None)
                self.ready.pop(nid, None)
            elif task.done():
                del self.pending[nid]
                self.pending_since.pop(nid, None)
                if not task.cancelled() and task.exception() is None:
                    if task.result().get("type") == "none":
                        continue  # (it turned into something else, such as a conversation with the player)
                    self.ready[nid] = task.result()
                    if self.ready[nid]["source"] == "llm":
                        self._llm_backoff = 0  # LLM answers are arriving in time again

    async def _decide(self, v: NpcView, names: dict[int, str]):
        # The LLM picks from a shortlist: small models choose badly from long lists.
        keep = self.reflex.shortlist(v, self.cfg.llm_choices) if hasattr(self.reflex, "shortlist") \
            else list(range(len(v.actions)))
        short = dataclasses.replace(v, actions=[v.actions[i] for i in keep])
        idx = await self.brain.choose_action(short, names)
        return {"type": "act", "action": short.actions[idx], "source": "llm"}

    _same_action = staticmethod(same_action)

    def _apply_ready(self, v: NpcView, ready: dict, choices, sources, lines) -> bool:
        """Apply a decision that finished in the background, if it is still
        possible now (the world kept moving while the NPC was thinking)."""
        idx = next((i for i, a in enumerate(v.actions) if self._same_action(a, ready["action"])), None)
        if idx is None:
            return False
        act = v.actions[idx]
        if ready["type"] == "act":
            choices[v.id], sources[v.id] = (idx, 0), ready["source"]
            self._commit_plan(v, act)
            return True
        option = ready["option"] if ready["option"] in act["options"] else "talk"
        choices[v.id], sources[v.id] = (idx, act["options"].index(option)), ready["source"]
        lines[v.id] = {**ready["line"], "option": option}
        self.plans.pop(v.id, None)
        return True

    async def _recall(self, owner: int, other: int, k: int = 3):
        # Vector stores embed and query off the event loop; the in-memory store is instant.
        if getattr(self.memory, "blocking", False):
            return await asyncio.get_running_loop().run_in_executor(
                self._recall_pool, self.memory.recall, owner, other, k)
        return self.memory.recall(owner, other, k)

    def _talk_done(self, _task):
        self.llm_talks = max(0, self.llm_talks - 1)

    async def _interaction(self, views: dict[int, NpcView], nid: int, act: dict, source: str,
                           use_llm: bool = True) -> dict:
        me = views[nid]
        other = views.get(act["target"])
        if other is None:
            return {"type": "act", "action": {"kind": "sleep"}, "source": source}
        recalled = await self._recall(nid, other.id)
        news = list(self.news)
        # Courtship: a family is only on the table once both sides are fond of each other.
        fond = min(self.memory.grudge(nid, other.id), self.memory.grudge(other.id, nid))
        options = [o for o in act["options"] if o != "mate" or fond >= self.cfg.courtship]
        ix = Interaction(me, other, options, [m.text for m in recalled],
                         self.memory.grudge(nid, other.id), news)
        brain = self.brain if use_llm and not self.llm_paused() else self.reflex
        if brain is not self.reflex and "mate" in options:
            # Instinct decides about family, the LLM still writes every word. bonsai-2:2b chose
            # "mate" for 1 of 18 offers and refused 21 of 27 proposals, so LLM villages had no
            # children and died of old age. When the heuristic would propose, the LLM words a
            # proposal; whether the partner accepts is the heuristic's call (below).
            instinct, _ = await self.reflex.choose_interaction(ix)
            if instinct == "mate":
                ix.options = ["mate"]
        speaker = self.talker if other.id == self.possessed and brain is not self.reflex else brain
        option, said = await speaker.choose_interaction(ix)
        said_llm = not isinstance(said, Canned)  # the LLM brain hands back a Canned line when it fails
        said = clamp_words(said)
        their = await self._recall(other.id, nid)
        rev = Interaction(other, me, options, [m.text for m in their],
                          self.memory.grudge(other.id, nid), news)
        context = option
        if other.id == self.possessed:  # speaking to the player: the player answers
            return await self._player_answers(act, me, other, option, said, said_llm, source)
        judge = self.reflex if base_option(option) == "mate" else brain
        if INTERACTIONS[base_option(option)].consent and not await judge.consent(rev, option, said):
            what = {"mate": "start a family"}.get(base_option(option), base_option(option))
            self.memory.log(Memory(nid, other.id, self.state["tick"], "refused",
                                   f"{other.name} refused to {what} with me.", -1))
            context, option = f"refused_{base_option(option)}", "talk"
        # the listener always answers, so every encounter is an exchange, not a monologue
        replier = brain if hasattr(brain, "reply") else self.reflex
        reply = await replier.reply(rev, said, context)
        reply_llm = not isinstance(reply, Canned)
        if brain is not self.reflex:  # an LLM conversation whose call failed stays silent, never canned
            said = said if said_llm else ""
            reply = reply if reply_llm else ""
        return {"type": "interact", "action": act, "option": option, "source": source,
                "line": {"npc": nid, "target": other.id, "text": said, "reply": clamp_words(reply),
                         "llm": said_llm, "reply_llm": reply_llm}}

    # --- hybrid routing ----------------------------------------------------------
    def _llm_priority(self, v: NpcView, tick: int) -> tuple | None:
        """None = decide by reflex. Otherwise a sort key (lower = more deserving)."""
        if self.reflex is self.brain or self.llm_paused(tick):  # heuristic-only run, or backing off
            return None
        s = v.stats
        if len(v.actions) == 1 or s["energy"] < 20 or s["hunger"] < 30:
            return None                        # survival reflex: no deliberation needed
        last = self.last_llm.get(v.id, -10**9)
        if any(a["kind"] == "build" for a in v.actions) and tick - last >= self.cfg.llm_interval // 2:
            return (0, last, v.id)             # enough wood to build: the NPC decides what and when
        near = self._neighbours(v)
        if near and not near <= self.llm_neighbours.get(v.id, frozenset()):
            return (0, last, v.id)             # someone new is adjacent: a social moment
        if tick - last >= self.cfg.llm_interval:
            return (1, last, v.id)             # periodic goal-setting, most overdue first
        return None

    # --- plan commitment ---------------------------------------------------------
    # A "walk toward X" decision is a multi-tick goal. Re-asking the LLM on every
    # step mostly gets "keep walking" back, so the NPC sticks to the goal until it
    # arrives, the path disappears, or something worth reconsidering happens.

    @staticmethod
    def _neighbours(v: NpcView) -> frozenset[int]:
        return frozenset(a["target"] for a in v.actions if a["kind"] == "interact")

    @staticmethod
    def _matches(plan: dict, act: dict) -> bool:
        return same_action(plan, act)

    def _commit_plan(self, v: NpcView, act: dict):
        if ACTIONS[act["kind"]].walk:
            self.plans[v.id] = {**act, "ticks": 0}
        else:
            self.plans.pop(v.id, None)

    def _continue_plan(self, v: NpcView) -> int | None:
        plan = self.plans.get(v.id)
        if plan is None:
            return None
        s = v.stats
        health, neighbours = self._last_seen.get(v.id, (s["health"], frozenset()))
        dest = OBJECTS.get(plan.get("what"))  # walking to a place: is it food, is it a bed?
        to_food = dest is not None and dest.food > 0
        to_bed = dest is not None and dest.sleep_energy > 0
        interrupt = (
            plan["ticks"] >= self.cfg.plan_max_ticks                       # periodic rethink
            or s["health"] < health                                       # got hurt
            or not self._neighbours(v) <= neighbours                      # someone new is adjacent
            or (s["hunger"] < 25 and not to_food)                         # urgent needs
            or (s["energy"] < 15 and not to_bed)
        )
        idx = None if interrupt else next(
            (i for i, a in enumerate(v.actions) if self._matches(plan, a)), None)
        if idx is None:  # arrived, path gone, target dead, or interrupted
            self.plans.pop(v.id, None)
            return None
        plan["ticks"] += 1
        return idx

    def _remember(self, owner, other, tick, kind, text, valence):
        self.memory.log(Memory(owner, other, tick, kind, text, valence))

    def _process_events(self, events: list[dict], lines: dict[int, dict], tick: int) -> list[str]:
        nm = lambda i: self.meta[i].name if i in self.meta else f"#{i}"  # noqa: E731
        out = []
        self.quiet = set()
        for e in events:
            t, a = e["type"], e.get("npc", -1)
            if t == "arrive":
                if e.get("why") == "migrant":  # (the director tells the story of their coming)
                    out.append(f"🧳 {nm(a)} arrives from far away")
                    self.quiet.add(out[-1])
                else:
                    out.append(f"✨ {nm(a)} appears out of nowhere")
            elif t in NATURE_EVENTS:
                out.extend(self._nature_event(e, tick, nm))
            elif t == "levelup":
                spec = SKILLS[e["skill"]]
                role = next((n["role"] for n in self.state["npcs"] if n["id"] == a), "")
                if role and role != self.roles.get(a):  # known for something new: that is news
                    self.roles[a] = role
                    out.append(f"⭐ {nm(a)} is now known as a {SKILLS[role].role.lower()}")
                else:
                    out.append(f"⭐ {nm(a)} reaches {spec.label.lower()} level {e['level']}")
                    if e["level"] < 5:
                        self.quiet.add(out[-1])
            elif t == "attrup":
                spec = ATTRS[e["attr"]]
                out.append(f"⭐ {nm(a)}'s {spec.label.lower()} grows to {e['value']}")
                self.quiet.add(out[-1])
            elif t == "talk":
                said = lines.get(a, {}).get("text", "")
                reply = lines.get(a, {}).get("reply")
                b = e["target"]
                if said:
                    self._remember(a, b, tick, "talk", f'I told {nm(b)}: "{said}"', 1)
                    self._remember(b, a, tick, "talk", f'{nm(a)} told me: "{said}"', 1)
                else:  # a silent exchange (the LLM call failed) still counts as time spent together
                    self._remember(a, b, tick, "talk", f"I sat with {nm(b)} for a while.", 1)
                    self._remember(b, a, tick, "talk", f"{nm(a)} sat with me for a while.", 1)
                if reply:
                    self._remember(a, b, tick, "talk", f'{nm(b)} answered me: "{reply}"', 0)
                    self._remember(b, a, tick, "talk", f'I answered {nm(a)}: "{reply}"', 0)
                out.append(f"{nm(a)} talks to {nm(b)}")
                out.extend(self._maybe_convert(a, b, tick, nm))
            elif t == "attack":
                b = e["target"]
                self.totals["fights"] += 0 if e["retaliation"] else 1
                verb = "strikes back at" if e["retaliation"] else "attacks"
                if e["hit"]:
                    self._remember(b, a, tick, "attack", f"{nm(a)} attacked me and wounded me.", -4)
                    self._remember(a, b, tick, "attack", f"I hit {nm(b)} in a fight.", -1)
                    out.append(f"{nm(a)} {verb} {nm(b)} (-{e['damage']} hp)")
                else:
                    self._remember(b, a, tick, "attack", f"{nm(a)} tried to attack me.", -3)
                    out.append(f"{nm(a)} {verb} {nm(b)} and misses")
            elif t == "trade":
                b = e["target"]
                self.totals["trades"] += 1
                gave, got = items_text({e["gave"]: e["gave_n"]}), items_text({e["got"]: e["got_n"]})
                self._remember(a, b, tick, "trade", f"I traded {nm(b)} my {gave} for {got}. Fair deal.", 2)
                self._remember(b, a, tick, "trade", f"{nm(a)} traded me {gave} for my {got}. Fair deal.", 2)
                out.append(f"🤝 {nm(a)} trades {gave} for {nm(b)}'s {got}")
            elif t == "gift":
                b = e["target"]
                what = items_text({e["item"]: e["n"]})
                self._remember(b, a, tick, "gift", f"{nm(a)} gave me {what} and asked for nothing.", 3)
                self._remember(a, b, tick, "gift", f"I gave {nm(b)} {what}.", 1)
                out.append(f"🎁 {nm(a)} gives {nm(b)} {what}")
            elif t == "pickup":
                owner = e.get("owner", -1)
                if owner not in (-1, a) and owner in self.meta and any(n["id"] == owner and n["alive"]
                                                                       for n in self.state["npcs"]):
                    what = items_text(e["items"])  # someone else's: they will remember it
                    self._remember(owner, a, tick, "theft", f"{nm(a)} took the {what} I left on the ground.", -2)
                    self._remember(a, owner, tick, "theft", f"I took the {what} {nm(owner)} left lying around.", 0)
                    out.append(f"🫳 {nm(a)} takes {what} that {nm(owner)} left on the ground")
            elif t == "conceive":
                b = e["target"]
                for p, q in ((a, b), (b, a)):
                    self._remember(p, q, tick, "mate", f"{nm(q)} and I are going to have a child.", 3)
                out.append(f"💞 {nm(a)} and {nm(b)} are expecting a child")
            elif t == "birth":
                pa, pb = e["parents"]
                genome, mutated = splice(self.meta[pa].genome, self.meta[pb].genome, self.rng)
                look = splice_look(self.meta[pa].look, self.meta[pb].look, self.rng)
                persona = splice_persona(self.meta[pa].persona, self.meta[pb].persona, self.persona_rng)
                gen = max(self.meta[pa].generation, self.meta[pb].generation) + 1
                child = self._register(a, genome, generation=gen, parents=[pa, pb],
                                       born_tick=tick, mutated=mutated, look=look, persona=persona)
                self.totals["births"] += 1
                for p, q in ((pa, pb), (pb, pa)):
                    self._remember(p, q, tick, "mate", f"{nm(q)} and I had a child, {child.name}.", 1)
                    self._remember(p, a, tick, "family", f"{child.name} is my child.", 3)
                    self._remember(a, p, tick, "family", f"{nm(p)} is my parent.", 3)
                mut = f" (mutation: {', '.join(mutated)})" if mutated else ""
                out.append(f"👶 {child.name} is born to {nm(pa)} & {nm(pb)} "
                           f"[{', '.join(genome.traits())}]{mut}")
            elif t == "death":
                self.totals["deaths"] += 1
                if e["killer"] > 0:
                    self._remember(e["killer"], a, tick, "kill", f"I killed {nm(a)}.", -2)
                    out.append(f"💀 {nm(a)} was killed by {nm(e['killer'])}")
                elif e["cause"] == "old age":
                    out.append(f"🪦 {nm(a)} died peacefully of old age")
                elif e["cause"] == "lightning":
                    out.append(f"⚡ {nm(a)} was struck down by lightning from a clear sky")
                elif e["cause"] in DEATHS:
                    out.append(DEATHS[e["cause"]].format(nm(a)))
                else:
                    out.append(f"💀 {nm(a)} died of {e['cause']}")
                out.extend(self._funeral(a, tick, nm))
                out.extend(self._pass_on_home(a, nm))
            elif t == "build":
                spec = OBJECTS[e["what"]]
                if spec.work:  # a building site: the building comes later (see "built")
                    out.append(f"🏗️ {nm(a)} starts building {spec.label}")
                    self.quiet.add(out[-1])
            elif t == "built":
                spec, owner = OBJECTS[e["what"]], e.get("owner", a)
                if spec.stat:
                    self.totals[spec.stat] += 1
                helped = f" (finished by {nm(a)})" if owner != a and owner in self.meta and spec.work else ""
                where = f" in {self.village_name(e['village'])}" if e.get("village", -1) != -1 else ""
                if spec.home and owner in self.meta and owner not in self.homes:
                    self.homes[owner] = (e["x"], e["y"])
                    out.append(f"🏠 {nm(owner)} builds a home{where}{helped}")
                else:
                    label = f"another {spec.place}" if spec.home else spec.label
                    out.append(f"{spec.emoji} {nm(owner)} builds {label}{where}{helped}")
            elif t == "found":
                name = self.village_name(e["village"])
                if a in self.meta:
                    out.append(f"🏘️ {nm(a)} founds the village of {name}")
            elif t == "join":
                out.append(f"🏘️ {nm(a)} settles in {self.village_name(e['village'])}")
            elif t == "abandoned":
                out.append(f"🏚️ {self.village_name(e['village'])} stands empty: everyone there is gone")
            elif t == "fish" and e.get("caught"):
                out.append(f"🎣 {nm(a)} catches {e['caught']} fish")
                self.quiet.add(out[-1])
            elif t == "deposit":
                out.append(f"📦 {nm(a)} puts {e['n']} {ITEMS[e['what']].label} in the storehouse")
                self.quiet.add(out[-1])
            elif t == "withdraw":
                out.append(f"📦 {nm(a)} takes {e['n']} {ITEMS[e['what']].label} from the storehouse")
                self.quiet.add(out[-1])
            elif t == "pray":
                out.append(f"🙏 {nm(a)} prays at the shrine")
                self.quiet.add(out[-1])
            elif t == "craft":
                out.append(f"{nm(a)} crafts a {ITEMS[e['what']].label}")
            elif t == "sleep" and a not in self.homes and self.vacant:
                me = next((n for n in self.state["npcs"] if n["id"] == a), None)
                hut = next((h for h in sorted(self.vacant)
                            if me and abs(h[0] - me["x"]) + abs(h[1] - me["y"]) == 1), None)
                if hut:
                    self.vacant.discard(hut)
                    self.homes[a] = hut
                    out.append(f"🏠 {nm(a)} moves into an empty hut")
        return out

    def _funeral(self, dead: int, tick: int, nm) -> list[str]:
        """The village buries its dead: everyone there remembers it."""
        body = next((n for n in self.state["npcs"] if n["id"] == dead), None)
        vid = body.get("village", -1) if body else -1
        mourners = [n["id"] for n in self.state["npcs"] if n["alive"] and n.get("village") == vid and vid != -1]
        if not mourners:
            return []
        for m in mourners:
            fond = self.memory.grudge(m, dead) >= 2
            self._remember(m, dead, tick, "funeral", f"We buried {nm(dead)}." + (" I miss them." if fond else ""),
                           -1 if fond else 0)
        line = f"🕯️ {self.village_name(vid)} buries {nm(dead)}"
        self.quiet.add(line)
        return [line]

    def _nature_event(self, e: dict, tick: int, nm) -> list[str]:
        """The weather, the seasons, wild animals, fire and fever, as chronicle lines and memories."""
        t, a = e["type"], e.get("npc", -1)
        out, quiet = [], False
        if t == "season":
            out.append(SEASON_LINES[e["season"]])
            for n in self.alive():
                self._remember(n["id"], -1, tick, "season", f"{e['season'].capitalize()} came.", 0)
        elif t == "weather":
            out.append(WEATHER_LINES[e["weather"]])
            quiet = e["weather"] in ("clear", "fog")
        elif t == "hunt" and e["killed"]:
            beast = ANIMALS[e["what"]]
            out.append(f"🏹 {nm(a)} kills a wolf" if beast.hunter else f"🏹 {nm(a)} brings down {beast.label}")
            self._remember(a, -1, tick, "hunt", f"I brought down {beast.label}.", 1)
        elif t == "mauled" and e["hit"]:
            if tick - self.mauled.get(a, -10**9) > 60:  # (one line per attack, not one per bite)
                out.append(f"🐺 Wolves attack {nm(a)}")
                self._remember(a, -1, tick, "wolf", "A wolf went for me and tore into me.", -3)
            self.mauled[a] = tick
        elif t == "wolves":
            out.append("🐺 Wolves come out of the wild, hungry")
        elif t == "ignite" and e.get("cause") == "lightning":
            out.append("⚡ Lightning strikes a tree and sets it ablaze")
        elif t == "douse":
            out.append(f"💦 {nm(a)} beats out a fire")
            self._remember(a, -1, tick, "fire", "I beat out a fire before it could spread.", 1)
            quiet = True
        elif t == "burn" and a >= 0:
            self._remember(a, -1, tick, "fire", "I got burned by the fire.", -1)
        elif t == "sick":
            src = e.get("source", -1)
            if e["how"] == "caught" and src in self.meta:
                out.append(f"🤒 {nm(a)} catches a fever from {nm(src)}")
                self._remember(a, src, tick, "sick", f"I caught a fever off {nm(src)}.", -1)
            elif e["how"] == "cold":
                out.append(f"🤒 {nm(a)} catches a chill that turns into a fever")
                self._remember(a, -1, tick, "sick", "The cold got into my chest. I've got a fever.", -1)
            else:
                self._remember(a, -1, tick, "sick", "I've come down with a fever.", -1)
            quiet = True
        elif t == "recover":
            out.append(f"💪 {nm(a)} gets over the fever")
            self._remember(a, -1, tick, "sick", "My fever finally broke.", 1)
            quiet = True
        elif t == "teach":
            b, skill = e["target"], SKILLS[e["skill"]].label.lower()
            self._remember(b, a, tick, "teach", f"Old {nm(a)} showed me a thing or two about {skill}.", 1)
            self._remember(a, b, tick, "teach", f"I taught {nm(b)} about {skill}.", 1)
        if quiet:
            self.quiet.update(out)
        return out

    def _maybe_convert(self, a: int, b: int, tick: int, nm) -> list[str]:
        """Beliefs are contagious: a talk can convert the listener. Charismatic
        speakers preach better, paranoid listeners resist, enemies never convince."""
        pa, pb = self.meta[a].persona, self.meta[b].persona
        if not pa or not pb or pa["belief"] == pb["belief"] or self.memory.grudge(b, a) < 0:
            return []
        chance = self.cfg.convert_chance
        chance *= 2 if self.meta[a].genome.social == "Charismatic" else 1
        chance *= 0.5 + 0.1 * attr_of(self._stats.get(a, {}), "charisma")  # (average charisma: x1)
        chance *= 0.25 if self.meta[b].genome.social == "Paranoid" else 1
        vb = self._stats.get(b, {}).get("village", -1)  # a shrine in the village: beliefs spread faster
        chance *= 1.6 if self._village_info(vb).get("has", {}).get("shrine") else 1
        if self.persona_rng.random() >= chance:
            return []
        pb["belief"] = pa["belief"]
        belief = belief_text(pa)
        self._remember(b, a, tick, "convert", f"{nm(a)} opened my eyes: now I {belief[:1].lower() + belief[1:]}.", 2)
        return [f"🌀 {nm(a)} converts {nm(b)}: {nm(b)} now {belief}"]

    def _pass_on_home(self, dead: int, nm) -> list[str]:
        """A home goes to the eldest living child who has none; otherwise it
        stands empty until a homeless NPC sleeps beside it and moves in."""
        home = self.homes.pop(dead, None)
        if home is None:
            return []
        living = {n["id"] for n in self.state["npcs"] if n["alive"]}
        heirs = sorted(i for i, m in self.meta.items() if dead in m.parents and i in living and i not in self.homes)
        if heirs:
            self.homes[heirs[0]] = home
            return [f"🏠 {nm(heirs[0])} inherits {nm(dead)}'s home"]
        self.vacant.add(home)
        return []

    # --- 7. render frame ----------------------------------------------------------
    def _frame(self, lines: dict[int, dict], log_lines: list[str]) -> dict:
        tick = self.state["tick"]
        action_emoji: dict[int, str] = {}
        for e in self.state["events"]:
            emoji = EVENT_EMOJI.get(e["type"])
            if not emoji:
                continue
            if e["type"] == "birth":
                for p in e["parents"]:
                    action_emoji[p] = emoji
            elif e["type"] == "conceive":
                action_emoji[e["npc"]] = action_emoji[e["target"]] = emoji
            elif e["type"] == "build":
                action_emoji[e["npc"]] = OBJECTS[e["what"]].emoji or emoji
            elif e["type"] == "craft":
                action_emoji[e["npc"]] = ITEMS[e["what"]].emoji or emoji
            elif e["type"] == "eat" and e.get("cooked"):
                action_emoji[e["npc"]] = "🍲"
            elif e["type"] == "attack" and not e["retaliation"]:
                action_emoji[e["npc"]] = emoji
            elif e["type"] not in ("attack",):
                action_emoji[e["npc"]] = emoji
        died_now = {e["npc"] for e in self.state["events"] if e["type"] == "death"}

        npcs = []
        for n in self.state["npcs"]:
            if not n["alive"] and n["id"] not in died_now:
                continue
            m = self.meta[n["id"]]
            nid = n["id"]
            busy = self.busy_until.get(nid, 0) > tick
            doing = self.activity.get(nid, "") if busy or nid in self.pending else ""
            resting = doing == "sleep" and busy
            work = self.working.get(nid)
            if not n["alive"]:
                status = "💀"
            elif nid in self.pending:
                status = "💭"
            elif n["asleep"] or resting or n["energy"] == 0:
                status = "💤"
            elif self.chat is not None and nid in (self.chat["with"], self.possessed):
                status = "💬"  # in conversation with the player
            elif work is not None:  # mid-task: show the tool in hand
                what = work["act"].get("what")
                status = ((OBJECTS[what].emoji if what in OBJECTS else ITEMS[what].emoji if what in ITEMS else "")
                          or EVENT_EMOJI.get(work["act"]["kind"], ""))
            elif n["hunger"] < 25:
                status = "🤤"
            elif n["expecting"]:
                status = "🤰"
            else:
                status = ""
            npcs.append({
                **{k: n[k] for k in ("id", "x", "y", "alive", "health", "hunger", "energy", "social",
                                     "inv", "cause", "attrs", "skills", "xp", "role", "caps")},
                "age": years(n["age"]),
                "name": m.name, "traits": m.genome.to_dict(), "generation": m.generation,
                "belief": belief_text(m.persona) if m.persona else "",
                "voice": voice_text(m.persona) if m.persona else "",
                "parents": m.parents, "mutated": m.mutated,
                "emoji": action_emoji.get(n["id"], ""), "status": status,
                "intent": "Thinking…" if nid in self.pending else self.intent.get(nid, ""),
                "source": self.source.get(nid, ""),
                # ticks the action started this tick will take (0 = none started); the
                # front end uses it to animate each NPC at its own speed
                "dur": self.started.get(nid, 0), "pace": m.pace,
                # what it is busy with and from which tick to which, for the time bar
                "doing": doing, "t0": self.busy_from.get(nid, 0) if busy else 0,
                "t1": self.busy_until.get(nid, 0) if busy else 0,
                "expecting": round(100 * (1 - n["expecting"] / GESTATION)) if n["expecting"] else 0,
                "look": m.look, "home": list(self.homes[nid]) if nid in self.homes else None,
                "stage": "child" if n["age"] < MATURE_AGE else ("elder" if n["age"] >= ELDER_AGE else "adult"),
                "village": n.get("village", -1),
            })

        dialogue = []
        for d in lines.values():
            if d and (d.get("text") or d.get("player")):
                if d.get("text"):
                    dialogue.append({"npc": d["npc"], "target": d["target"], "text": d["text"], "tick": tick,
                                     "kind": base_option(d["option"]), "llm": d.get("llm", False),
                                     "player": d.get("player", False),
                                     "deal": option_text(d["option"]) if ":" in d["option"] else ""})
                if d.get("reply"):
                    dialogue.append({"npc": d["target"], "target": d["npc"], "text": d["reply"], "tick": tick,
                                     "kind": "talk", "reply": True, "llm": d.get("reply_llm", False),
                                     "player": d.get("player_reply", False)})

        for r in self.voice_lines:  # the voice from the sky, and what was said back to it
            dialogue.append({"npc": -1, "target": r["npc"], "text": r["said"], "tick": tick, "kind": "voice",
                             "llm": False, "player": True})
            dialogue.append({"npc": r["npc"], "target": -1, "text": r["reply"], "tick": tick, "kind": "voice",
                             "reply": True, "llm": r["llm"]})
        self.voice_lines = []
        dialogue.extend(self.chat_out)  # lines of the player's conversation
        self.chat_out = []
        world = getattr(self.engine, "world", None)
        frame = {
            "type": "frame", "tick": tick, "width": self.state["width"], "height": self.state["height"],
            # "world" tells one world from the next; "start" is where its founders settled
            "world": self.epoch, "start": list(world.start) if world is not None else None,
            # The map, one string per row: "terrain" is the ground, "tiles" what each tile shows
            # (the object standing on it, or else the ground). "trev" and "rev" change whenever
            # they do; the server only sends them again when they have.
            "rev": self.state["rev"], "tiles": self.state["tiles"],
            "trev": self.state["trev"], "terrain": self.state["terrain"],
            "drops": self.state["drops"],  # things lying on the ground: [x, y, {item: count}]
            "npcs": npcs, "dialogue": dialogue, "log": log_lines,
            # this tick's engine events, so the front end can animate swings, hits, chops...
            "fx": [e for e in self.state["events"] if e["type"] in FX_EVENTS] + self.work_fx,
            "stats": {"population": len(self.alive()), "memories": self.memory.count(),
                      "max_generation": max((m.generation for m in self.meta.values()), default=0),
                      "decisions": dict(self.decision_stats),
                      **self.totals},
            "brain": getattr(self.brain, "name", "?") + (" (paused: slow)" if self.llm_paused(tick) else ""),
            "talker": getattr(self.talker, "name", "?"),
            "map": self.cfg.map_name,
            "possessed": self.possessed, "player": self._player_frame(),
            "sites": self.state.get("sites", []),
            "villages": [{**v, "name": self.village_name(v["id"]),
                          "relations": {str(b): r for (a, b), r in self.relations.items() if a == v["id"]}}
                         for v in self.state.get("villages", [])],
            "memory": getattr(self.memory, "name", "?"),
            "clock": self.state.get("clock", {}),
            "animals": self.state.get("animals", []),
            "happenings": self.director.happened,  # this tick's big events (wildfire, festival...)
        }
        self.last_frame = frame
        return frame
