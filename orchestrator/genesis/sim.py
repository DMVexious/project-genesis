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
import logging
import random
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .brain import Canned, Interaction, NpcView, clamp_words, describe_action
from .memory import Memory
from .engine_py import ELDER_AGE, MATURE_AGE
from .traits import (Genome, belief_text, make_name, random_genome, random_look, random_persona, splice,
                     splice_look, splice_persona, voice_text)

log = logging.getLogger(__name__)

ACTION_EMOJI = {"chop": "🪓", "build": "🔨", "attack": "💢", "birth": "❤️", "forage": "🍒",
                "eat": "🍖", "trade": "🤝", "talk": "💬", "craft": "🗡️", "death": "💀"}
BUILD_EMOJI = {"hut": "🔨", "farm": "🌱", "fire": "🔥"}

# Base duration of each action in ticks, before the NPC's pace and random jitter.
ACTION_TICKS = {"goto": 2, "goto_npc": 2, "wander": 3, "chop": 5, "forage": 3, "eat": 2,
                "sleep": 7, "build:hut": 10, "build:farm": 6, "build:fire": 4, "craft_spear": 6,
                "interact": 4}


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


@dataclass
class SimConfig:
    seed: int = 7
    width: int = 80
    height: int = 56
    population: int = 16
    decay_every: int = 3      # ticks per unit of world time (hunger, energy, ageing, regrowth)
    plan_max_ticks: int = 12  # re-ask the brain at least this often while walking (steps)
    llm_interval: int = 18    # ticks between an NPC's LLM goal checks
    llm_budget: int = 2       # max brain requests in flight at once
    talk_budget: int = 3      # max conversations using the LLM at once (each makes up to 5 calls);
                              # extra ones talk on reflex so the LLM queue never piles up
    think_max_ticks: int = 40 # give up on a brain call after this many ticks (~6 s) and act on reflex
    convert_chance: float = 0.08  # chance a conversation spreads the speaker's belief to the listener


class Simulation:
    def __init__(self, engine, brain, memory, config: SimConfig):
        # Memory embedding/queries get one dedicated worker thread. Using the
        # shared default pool let a backlog of slow embeddings starve it, and
        # asyncio resolves host names on that pool, so LLM requests stalled too.
        self._mem_pool = ThreadPoolExecutor(1, thread_name_prefix="memory")
        self._recall_pool = ThreadPoolExecutor(1, thread_name_prefix="recall")  # never queued behind a flush
        self.engine, self.brain, self.memory, self.cfg = engine, brain, memory, config
        # LLM brains carry a heuristic fallback; that same brain handles reflexes.
        self.reflex = getattr(brain, "fallback", brain)
        self.reset(config.seed)

    def reset(self, seed: int | None = None):
        if seed is not None:
            self.cfg.seed = seed
        self.rng = random.Random(self.cfg.seed)
        # personas draw from their own stream so they don't shift the world's dice
        self.persona_rng = random.Random(self.cfg.seed * 1_000_003 + 17)
        self.news: deque[str] = deque(maxlen=6)  # recent village events NPCs gossip about
        self.memory.reset()
        for task in getattr(self, "pending", {}).values():
            task.cancel()
        self.state = self.engine.init(self.cfg.seed, self.cfg.width, self.cfg.height,
                                      self.cfg.population, self.cfg.decay_every)
        self.meta: dict[int, NpcMeta] = {}
        for n in self.state["npcs"]:
            self._register(n["id"], random_genome(self.rng), look=random_look(self.rng),
                           persona=random_persona(self.persona_rng))
        self.totals = {"births": 0, "deaths": 0, "huts": 0, "farms": 0, "fires": 0, "fights": 0, "trades": 0}
        self.homes: dict[int, tuple[int, int]] = {}  # NPC -> its home hut (built, inherited or claimed)
        self.vacant: set[tuple[int, int]] = set()    # huts whose owner died without an heir
        self.dialogue: list[dict] = []
        self.plans: dict[int, dict] = {}
        self.decision_stats = {"llm": 0, "reflex": 0, "plan": 0}
        self.last_llm: dict[int, int] = {}
        self.llm_neighbours: dict[int, frozenset] = {}  # who was adjacent at the last LLM decision
        self._last_seen: dict[int, tuple[int, frozenset]] = {}
        self.pending: dict[int, asyncio.Future] = {}   # brain calls in flight
        self.pending_since: dict[int, int] = {}        # tick each call was started
        self.pending_kind: dict[int, str] = {}         # "decide" (pure LLM call) or "interact"
        self.llm_talks = 0  # conversations in flight that are using the LLM
        self._flush_task: asyncio.Future | None = None
        self.llm_paused_until = 0  # backoff after the LLM missed a deadline
        self._llm_backoff = 0
        self.ready: dict[int, dict] = {}               # finished decisions waiting to be applied
        self.busy_until: dict[int, int] = {}           # tick each NPC may act again
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

    def view(self, n: dict) -> NpcView:
        m = self.meta[n["id"]]
        x0, y0 = n["x"], n["y"]
        rows = [row[max(0, x0 - 5):x0 + 6] for row in self.state["tiles"][max(0, y0 - 5):y0 + 6]]
        count = lambda chars: sum(r.count(c) for r in rows for c in chars)  # noqa: E731
        nearby = {"hut": count("H"), "farm": count("Ff"), "fire": count("C")}
        home = self.homes.get(n["id"])
        dist = None if home is None else abs(home[0] - x0) + abs(home[1] - y0)
        return NpcView(n["id"], m.name, m.genome, n, self.state["actions"].get(str(n["id"]), []),
                       nearby["hut"], nearby, dist, n["age"] >= ELDER_AGE, m.persona)

    def alive(self) -> list[dict]:
        return [n for n in self.state["npcs"] if n["alive"]]

    # --- one tick ------------------------------------------------------------
    # NPCs are asynchronous: every action lasts a random number of ticks scaled
    # by the NPC's own pace, so they act out of step with each other. Brain
    # calls run as background tasks: the world never waits for inference. An
    # NPC that is "thinking" just pauses until its answer lands.

    async def tick(self) -> dict:
        await asyncio.sleep(0)  # let finished brain tasks deliver their results
        names = self.names()
        living = {n["id"]: n for n in self.alive()}
        views = {nid: self.view(n) for nid, n in living.items()}
        now = self.state["tick"] + 1  # the tick about to be executed
        self._harvest(living)

        choices: dict[int, tuple[int, int]] = {}
        sources: dict[int, str] = {}
        lines: dict[int, dict] = {}
        candidates: list[tuple[tuple, NpcView]] = []
        for nid, v in views.items():
            if self.busy_until.get(nid, 0) > now:
                continue
            if nid in self.pending:
                if not self._give_up_thinking(v, now):
                    continue
                self._reflex(v, choices, sources)  # too slow or in danger: act now
                continue
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
                del choices[nid]
                use_llm = self.reflex is not self.brain and self.llm_talks < self.cfg.talk_budget
                self._spawn(nid, self._interaction(views, nid, act, sources.pop(nid, "reflex"), use_llm), "interact")
                if use_llm:  # released when the task ends, even if it is cancelled before it starts
                    self.llm_talks += 1
                    self.pending[nid].add_done_callback(self._talk_done)

        for nid in choices:
            v = views[nid]
            self.decision_stats[sources[nid]] += 1
            self.intent[nid] = describe_action(v.actions[choices[nid][0]], names, living[nid])
            self.source[nid] = sources[nid]
            self._last_seen[nid] = (living[nid]["health"], self._neighbours(v))

        # 5. Execution.
        self.state = self.engine.step(choices)
        self.started = {}
        for nid, (idx, _) in choices.items():
            act = views[nid].actions[idx]
            kind = act["kind"]
            dur = self._duration(nid, f"build:{act['what']}" if kind == "build" else kind)
            self.busy_until[nid] = now + dur
            self.activity[nid] = kind
            self.started[nid] = dur

        # 6. Memory logging + births.
        log_lines = self._process_events(self.state["events"], lines, now)
        for line in log_lines:
            if " talks to " not in line:
                self.news.append(line.lstrip("💀🪦🏠🔨🌱🔥🌀 ").strip())
        if now % 10 == 0:  # embedding is costly; batch it (recall() flushes first anyway)
            self._flush_memory()
        return self._frame(lines, log_lines)

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

    def _duration(self, nid: int, kind: str) -> int:
        base = ACTION_TICKS.get(kind, 2)
        return max(1, round(base * self.meta[nid].pace * self.rng.uniform(0.75, 1.35)))

    def _reflex(self, v: NpcView, choices, sources):
        # What to build is a deliberate choice: while an LLM is answering, only it
        # decides to build. Reflexes build only in heuristic-only runs or while
        # the LLM is paused for being too slow.
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
                    self.ready[nid] = task.result()
                    if self.ready[nid]["source"] == "llm":
                        self._llm_backoff = 0  # LLM answers are arriving in time again

    async def _decide(self, v: NpcView, names: dict[int, str]):
        idx = await self.brain.choose_action(v, names)
        return {"type": "act", "action": v.actions[idx], "source": "llm"}

    @staticmethod
    def _same_action(a: dict, b: dict) -> bool:
        if a["kind"] != b["kind"]:
            return False
        if a["kind"] in ("goto", "build"):
            return a["what"] == b["what"]
        if a["kind"] in ("goto_npc", "interact"):
            return a["target"] == b["target"]
        return True

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
        ix = Interaction(me, other, act["options"], [m.text for m in recalled],
                         self.memory.grudge(nid, other.id), news)
        brain = self.brain if use_llm and not self.llm_paused() else self.reflex
        option, said = await brain.choose_interaction(ix)
        said_llm = not isinstance(said, Canned)  # the LLM brain hands back a Canned line when it fails
        said = clamp_words(said)
        their = await self._recall(other.id, nid)
        rev = Interaction(other, me, act["options"], [m.text for m in their],
                          self.memory.grudge(other.id, nid), news)
        context = option
        if option in ("trade", "mate") and not await brain.consent(rev, option, said):
            self.memory.log(Memory(nid, other.id, self.state["tick"], "refused",
                                   f"{other.name} refused to {option} with me.", -1))
            context, option = f"refused_{option}", "talk"
        # the listener always answers, so every encounter is an exchange, not a monologue
        replier = brain if hasattr(brain, "reply") else self.reflex
        reply = await replier.reply(rev, said, context)
        reply_llm = not isinstance(reply, Canned)
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
        if plan["kind"] != act["kind"]:
            return False
        if plan["kind"] == "goto":
            return act["what"] == plan["what"]
        return act["target"] == plan["target"]

    def _commit_plan(self, v: NpcView, act: dict):
        if act["kind"] == "goto" or act["kind"] == "goto_npc":
            self.plans[v.id] = {**act, "ticks": 0}
        else:
            self.plans.pop(v.id, None)

    def _continue_plan(self, v: NpcView) -> int | None:
        plan = self.plans.get(v.id)
        if plan is None:
            return None
        s = v.stats
        health, neighbours = self._last_seen.get(v.id, (s["health"], frozenset()))
        interrupt = (
            plan["ticks"] >= self.cfg.plan_max_ticks                       # periodic rethink
            or s["health"] < health                                       # got hurt
            or not self._neighbours(v) <= neighbours                      # someone new is adjacent
            or (s["hunger"] < 25 and plan.get("what") != "bush")          # urgent needs
            or (s["energy"] < 15 and plan.get("what") != "hut")
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
        for e in events:
            t, a = e["type"], e["npc"]
            if t == "talk":
                said = lines.get(a, {}).get("text", "")
                reply = lines.get(a, {}).get("reply")
                b = e["target"]
                self._remember(a, b, tick, "talk", f'I told {nm(b)}: "{said}"', 1)
                self._remember(b, a, tick, "talk", f'{nm(a)} told me: "{said}"', 1)
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
                self._remember(a, b, tick, "trade", f"I traded fairly with {nm(b)}.", 2)
                self._remember(b, a, tick, "trade", f"{nm(a)} traded fairly with me.", 2)
                out.append(f"{nm(a)} trades {e['gave']} with {nm(b)}")
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
                    self._remember(p, q, tick, "mate", f"{nm(q)} and I had a child, {child.name}.", 3)
                    self._remember(p, a, tick, "family", f"{child.name} is my child.", 3)
                    self._remember(a, p, tick, "family", f"{nm(p)} is my parent.", 3)
                mut = f" (mutation: {', '.join(mutated)})" if mutated else ""
                out.append(f"{child.name} is born to {nm(pa)} & {nm(pb)} "
                           f"[{', '.join(genome.traits())}]{mut}")
            elif t == "death":
                self.totals["deaths"] += 1
                if e["killer"] > 0:
                    self._remember(e["killer"], a, tick, "kill", f"I killed {nm(a)}.", -2)
                    out.append(f"💀 {nm(a)} was killed by {nm(e['killer'])}")
                elif e["cause"] == "old age":
                    out.append(f"🪦 {nm(a)} died peacefully of old age")
                else:
                    out.append(f"💀 {nm(a)} died of {e['cause']}")
                out.extend(self._pass_on_home(a, nm))
            elif t == "build":
                what = e.get("what", "hut")
                self.totals[{"hut": "huts", "farm": "farms", "fire": "fires"}[what]] += 1
                if what == "hut" and a not in self.homes:
                    self.homes[a] = (e["x"], e["y"])
                    out.append(f"🏠 {nm(a)} builds a home at ({e['x']},{e['y']})")
                else:
                    label = {"hut": "another hut", "farm": "a farm", "fire": "a campfire"}[what]
                    out.append(f"{BUILD_EMOJI[what]} {nm(a)} builds {label} at ({e['x']},{e['y']})")
            elif t == "craft":
                out.append(f"{nm(a)} crafts a spear")
            elif t == "sleep" and a not in self.homes and self.vacant:
                me = next((n for n in self.state["npcs"] if n["id"] == a), None)
                hut = next((h for h in sorted(self.vacant)
                            if me and abs(h[0] - me["x"]) + abs(h[1] - me["y"]) == 1), None)
                if hut:
                    self.vacant.discard(hut)
                    self.homes[a] = hut
                    out.append(f"🏠 {nm(a)} moves into an empty hut")
        return out

    def _maybe_convert(self, a: int, b: int, tick: int, nm) -> list[str]:
        """Beliefs are contagious: a talk can convert the listener. Charismatic
        speakers preach better, paranoid listeners resist, enemies never convince."""
        pa, pb = self.meta[a].persona, self.meta[b].persona
        if not pa or not pb or pa["belief"] == pb["belief"] or self.memory.grudge(b, a) < 0:
            return []
        chance = self.cfg.convert_chance
        chance *= 2 if self.meta[a].genome.social == "Charismatic" else 1
        chance *= 0.25 if self.meta[b].genome.social == "Paranoid" else 1
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
            emoji = ACTION_EMOJI.get(e["type"])
            if not emoji:
                continue
            if e["type"] == "birth":
                for p in e["parents"]:
                    action_emoji[p] = emoji
            elif e["type"] == "build":
                action_emoji[e["npc"]] = BUILD_EMOJI.get(e.get("what"), emoji)
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
            doing = self.activity.get(nid, "")
            resting = doing == "sleep" and self.busy_until.get(nid, 0) > tick
            if not n["alive"]:
                status = "💀"
            elif nid in self.pending:
                status = "💭"
            elif n["asleep"] or resting or n["energy"] == 0:
                status = "💤"
            elif n["hunger"] < 25:
                status = "🤤"
            else:
                status = ""
            npcs.append({
                **{k: n[k] for k in ("id", "x", "y", "alive", "health", "hunger", "energy", "social",
                                     "wood", "food", "spear", "age", "cause")},
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
                "look": m.look, "home": list(self.homes[nid]) if nid in self.homes else None,
                "stage": "child" if n["age"] < MATURE_AGE else ("elder" if n["age"] >= ELDER_AGE else "adult"),
            })

        dialogue = []
        for d in lines.values():
            if d:
                dialogue.append({"npc": d["npc"], "target": d["target"], "text": d["text"], "tick": tick,
                                 "kind": d["option"], "llm": d.get("llm", False)})
                if d.get("reply"):
                    dialogue.append({"npc": d["target"], "target": d["npc"], "text": d["reply"], "tick": tick,
                                     "kind": "talk", "reply": True, "llm": d.get("reply_llm", False)})

        frame = {
            "type": "frame", "tick": tick, "width": self.state["width"], "height": self.state["height"],
            "tiles": self.state["tiles"], "npcs": npcs, "dialogue": dialogue, "log": log_lines,
            "stats": {"population": len(self.alive()), "memories": self.memory.count(),
                      "max_generation": max((m.generation for m in self.meta.values()), default=0),
                      "decisions": dict(self.decision_stats),
                      **self.totals},
            "brain": getattr(self.brain, "name", "?") + (" (paused: slow)" if self.llm_paused(tick) else ""),
            "engine": getattr(self.engine, "name", "?"),
            "memory": getattr(self.memory, "name", "?"),
        }
        self.last_frame = frame
        return frame
